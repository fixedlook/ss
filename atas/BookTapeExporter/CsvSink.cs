using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;

namespace Atas.Indicators.BookTape
{
	/// <summary>
	/// Append-only CSV writer: one file per (stream, session day, part).
	///
	/// Called from the data-feed thread on every event, so enqueueing is the only work done there.
	/// Formatting and disk I/O happen on a single background pump thread.
	///
	/// Session day is not the UTC date: a Globex session starting at 18:00 UTC belongs to the *next*
	/// trading day, so rows are attributed to <c>(sessionTime - rollHours).Date</c>. Without this a
	/// session is split across two files at midnight, which is exactly the bug that makes overnight
	/// data useless for replay.
	/// </summary>
	internal sealed class CsvSink : IDisposable
	{
		private const int MaxQueuedRows = 3_000_000;

		private readonly struct PendingRow
		{
			public PendingRow(string dayKey, string text)
			{
				DayKey = dayKey;
				Text = text;
			}

			public string DayKey { get; }

			public string Text { get; }
		}

		private readonly ConcurrentQueue<PendingRow> _queue = new();
		private readonly Thread _pump;
		private readonly string _directory;
		private readonly string _streamName;
		private readonly string _headerRow;
		private readonly string _sessionZoneId;
		private readonly int _rollHour;
		private readonly long _maxRowsPerFile;

		private volatile bool _stop;
		private int _queuedCount;
		private long _writtenRows;
		private long _droppedRows;
		private long _writeErrors;
		private TimeZoneInfo? _zone;

		public CsvSink(
			string directory,
			string streamName,
			string headerRow,
			string sessionZoneId,
			int rollHour,
			long maxRowsPerFile)
		{
			_directory = directory;
			_streamName = streamName;
			_headerRow = headerRow;
			_sessionZoneId = sessionZoneId;
			_rollHour = rollHour;
			_maxRowsPerFile = maxRowsPerFile <= 0 ? long.MaxValue : maxRowsPerFile;

			try
			{
				_zone = TimeZoneInfo.FindSystemTimeZoneById(sessionZoneId);
			}
			catch (Exception)
			{
				_zone = TimeZoneInfo.Utc;
			}

			Directory.CreateDirectory(directory);

			_pump = new Thread(PumpLoop)
			{
				IsBackground = true,
				Name = "BookTape-" + streamName,
				Priority = ThreadPriority.BelowNormal
			};
			_pump.Start();
		}

		public long WrittenRows => Interlocked.Read(ref _writtenRows);

		public long DroppedRows => Interlocked.Read(ref _droppedRows);

		public long WriteErrors => Interlocked.Read(ref _writeErrors);

		public int QueuedRows => Volatile.Read(ref _queuedCount);

		/// <summary>
		/// Maps an instant that is <b>already UTC</b> to the session-local wall clock and the session day.
		/// </summary>
		/// <remarks>
		/// The caller owns the UTC conversion (it needs the instrument's offset for that), and this method
		/// never re-guesses it. Having a single place that decides the instant is what keeps ts_utc and
		/// ts_local describing the same moment - two independent conversions drift apart whenever the
		/// broker zone differs from the session zone.
		/// </remarks>
		public (DateTime Session, string DayKey) Localise(DateTime utc)
		{
			var session = TimeZoneInfo.ConvertTimeFromUtc(DateTime.SpecifyKind(utc, DateTimeKind.Utc), _zone ?? TimeZoneInfo.Utc);
			return (session, SessionDayKey(session));
		}

		public string SessionDayKey(DateTime sessionTime)
		{
			var shifted = sessionTime.AddHours(-_rollHour);
			return shifted.ToString("yyyyMMdd", CultureInfo.InvariantCulture);
		}

		public static string Fmt(DateTime value)
		{
			return value.ToString("yyyy-MM-dd HH:mm:ss.fff", CultureInfo.InvariantCulture);
		}

		public static string Fmt(decimal value)
		{
			return value.ToString("0.####", CultureInfo.InvariantCulture);
		}

		public void Enqueue(string dayKey, string? row)
		{
			if (row is null)
				return;

			if (Volatile.Read(ref _queuedCount) >= MaxQueuedRows)
			{
				Interlocked.Increment(ref _droppedRows);
				return;
			}

			Interlocked.Increment(ref _queuedCount);
			_queue.Enqueue(new PendingRow(dayKey, row));
		}

		private void PumpLoop()
		{
			var batch = new StringBuilder(1 << 20);
			string? currentFileId = null;
			long rowsInFile = 0;
			StreamWriter? file = null;

			while (!_stop || !_queue.IsEmpty)
			{
				if (_queue.IsEmpty)
				{
					if (_stop)
						break;

					Flush(file);
					Thread.Sleep(200);
					continue;
				}

				batch.Clear();
				var consumed = 0;

				while (consumed < 100_000 && _queue.TryDequeue(out var pending))
				{
					Interlocked.Decrement(ref _queuedCount);
					consumed++;

					// The part number grows with the row count so a month of ticks stays in openable
					// chunks instead of one multi-gigabyte file.
					var part = rowsInFile / _maxRowsPerFile;
					var fileId = pending.DayKey + "_p" + part.ToString(CultureInfo.InvariantCulture);

					if (fileId != currentFileId)
					{
						DisposeFile(file);

						rowsInFile = part * _maxRowsPerFile;
						currentFileId = fileId;
						file = OpenFile(pending.DayKey, part);

						if (file is null)
						{
							Interlocked.Increment(ref _droppedRows);
							continue;
						}
					}

					batch.Append(pending.Text).Append('\n');
					rowsInFile++;
				}

				if (file is null || batch.Length == 0)
					continue;

				try
				{
					file.Write(batch);
					file.Flush();
					Interlocked.Add(ref _writtenRows, consumed);
				}
				catch (Exception)
				{
					Interlocked.Increment(ref _writeErrors);
					Interlocked.Add(ref _droppedRows, consumed);
				}
			}

			DisposeFile(file);
		}

		private static void Flush(StreamWriter? file)
		{
			try
			{
				file?.Flush();
			}
			catch (Exception)
			{
				// Reported by the next write.
			}
		}

		private static void DisposeFile(StreamWriter? file)
		{
			try
			{
				file?.Flush();
				file?.Dispose();
			}
			catch (Exception)
			{
				// Best effort on rollover.
			}
		}

		private StreamWriter? OpenFile(string dayKey, long part)
		{
			var suffix = part > 0 ? "_p" + part.ToString(CultureInfo.InvariantCulture) : string.Empty;
			var path = Path.Combine(_directory, $"{_streamName}_{dayKey}{suffix}.csv");
			var exists = File.Exists(path);

			try
			{
				var stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read, 1 << 16);
				var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = false };

				if (!exists)
					writer.WriteLine(_headerRow);

				return writer;
			}
			catch (Exception)
			{
				Interlocked.Increment(ref _writeErrors);
				return null;
			}
		}

		public void Dispose()
		{
			_stop = true;
			_pump.Join(TimeSpan.FromSeconds(30));
		}
	}
}
