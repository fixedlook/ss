using System;
using System.IO;

namespace GexWalls
{
    /// <summary>
    /// Minimal file logger. Kept dependency-free on purpose: the indicator has to
    /// build against ATAS.Indicators / OFT.Rendering only, and "why is my chart
    /// empty" needs an answer someone can actually read.
    ///
    /// %APPDATA%\ATAS X\GexWalls\log.txt
    /// </summary>
    public static class GexLog
    {
        private const long MaxBytes = 512 * 1024;
        private static readonly object Gate = new object();

        public static string LogPath => Path.Combine(LocalLevelsFile.DataDirectory, "log.txt");

        public static void Write(string message)
        {
            try
            {
                lock (Gate)
                {
                    Directory.CreateDirectory(LocalLevelsFile.DataDirectory);

                    var path = LogPath;
                    var info = new FileInfo(path);
                    if (info.Exists && info.Length > MaxBytes)
                        File.Delete(path);

                    File.AppendAllText(path,
                        DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + message + Environment.NewLine);
                }
            }
            catch
            {
                // logging must never break the indicator
            }
        }
    }
}
