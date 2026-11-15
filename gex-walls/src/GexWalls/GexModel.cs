using System;
using System.Collections.Generic;

namespace GexWalls
{
    /// <summary>One option contract as delivered by the CBOE delayed chain.</summary>
    public sealed class OptionContract
    {
        public string Symbol = "";
        public string Root = "";
        public DateTime Expiry;
        public bool IsCall;
        public decimal Strike;

        public decimal Bid, Ask, Last;
        public decimal Iv, Delta, Gamma, Theta, Vega;
        public decimal OpenInterest, Volume;

        public decimal Mid =>
            Bid > 0 && Ask > 0 ? (Bid + Ask) / 2m : (Last > 0 ? Last : (Ask > 0 ? Ask : Bid));

        public int DaysToExpiry(DateTime today) => (int)(Expiry.Date - today.Date).TotalDays;
    }

    /// <summary>A parsed option chain for one underlying, before any filtering.</summary>
    public sealed class ChainSnapshot
    {
        public string Source = "";
        public decimal Spot;
        public decimal Iv30;
        public DateTime FetchedUtc = DateTime.UtcNow;
        public readonly List<OptionContract> Contracts = new();
        public string Status = "";
    }

    /// <summary>Knobs that control how the chain is aggregated into levels.</summary>
    public sealed class LevelOptions
    {
        /// <summary>Ignore expiries closer than this many days (0 keeps 0DTE).</summary>
        public int MinDte;

        /// <summary>Ignore expiries further out than this. 45 keeps the gamma-relevant part of the book.</summary>
        public int MaxDte = 45;

        /// <summary>Ignore strikes beyond this % distance from spot. 0 disables the filter.</summary>
        public decimal MaxStrikeDistancePct = 20m;

        /// <summary>Shares per contract.</summary>
        public decimal ContractMultiplier = 100m;

        /// <summary>How far out the expiry used for max pain / expected move may be.</summary>
        public int NearestExpiryMaxDte = 10;

        /// <summary>
        /// Open interest is T-1 until the overnight file lands, which makes today's
        /// expiry look empty. When true, volume is used as OI for 0DTE contracts.
        /// </summary>
        public bool VolumeProxyForToday;

        /// <summary>
        /// How far either side of spot the gamma-flip scan looks, in percent.
        /// The flip is where re-priced net gamma changes sign; a flip further out
        /// than this is noise from the wings, not a tradeable level.
        /// </summary>
        public decimal GammaFlipBandPct = 12m;

        /// <summary>Grid resolution of that scan (80 gives 0.3% steps at a 12% band).</summary>
        public int GammaFlipSteps = 80;

        /// <summary>
        /// A strike needs at least this share of the largest per-strike open
        /// interest to be eligible as an IV wall, so a single lot in the far wing
        /// cannot win on implied vol alone.
        /// </summary>
        public decimal IvWallMinOiFraction = 0.02m;
    }

    /// <summary>Result of the aggregation: the levels the indicator draws.</summary>
    public sealed class LevelsSnapshot
    {
        public string Source = "";
        public decimal Spot;
        public decimal Iv30;
        public DateTime UpdatedUtc = DateTime.UtcNow;
        public string Status = "";

        public decimal? ZeroGamma;
        public decimal? CallWall1, CallWall2;
        public decimal? PutWall1, PutWall2;
        public decimal? IvWall1, IvWall2;
        public decimal? MaxPain;
        public decimal? EmHigh, EmLow;

        public decimal TotalGex;
        public decimal? PutCallOiRatio;
        public int ContractsUsed;

        /// <summary>Everything needed to explain - or dispute - the numbers above.</summary>
        public ChainAudit Audit;

        public bool HasAnyLevel =>
            ZeroGamma.HasValue || CallWall1.HasValue || PutWall1.HasValue ||
            IvWall1.HasValue || MaxPain.HasValue;
    }

    /// <summary>
    /// Provenance for one computation: how many contracts were seen, how many
    /// survived each filter, which expiries contributed, and the per-strike table
    /// the levels were read off. Written to audit_&lt;source&gt;.json so a level can
    /// be compared strike-for-strike against another feed.
    /// </summary>
    public sealed class ChainAudit
    {
        public string Source = "";
        public decimal Spot;
        public int ContractsTotal;
        public int ContractsUsed;
        public int ExcludedExpired;
        public int ExcludedInvalidStrike;
        public int ExcludedDte;
        public int ExcludedStrike;
        public int ExcludedNoOiOrGamma;
        public int ExcludedNoIv;
        public string IvUnits = "";
        public DateTime? NearestExpiry;

        public List<ExpiryAudit> Expiries = new();
        public List<StrikeAudit> Strikes = new();

        // gamma flip scan
        public decimal? FlipValue;
        public decimal FlipBandLow;
        public decimal FlipBandHigh;
        public int FlipGridPoints;
        public int FlipCrossings;
        public string FlipNote = "";
        public decimal BsGexAtSpot;
        public decimal ChainGexAtSpot;

        /// <summary>Crossings of the cumulative-by-strike series, kept only to show why that proxy is wrong.</summary>
        public List<decimal> ChainCrossings = new();
    }

    public sealed class ExpiryAudit
    {
        public DateTime Expiry;
        public int Dte;
        public int Contracts;
        public decimal OpenInterest;
        public decimal Gex;
    }

    public sealed class StrikeAudit
    {
        public decimal Strike;
        public decimal CallOi, PutOi;
        public decimal CallGex, PutGex, Net, Cumulative;
        public decimal MeanIv;
    }

    /// <summary>How a level expressed in the option underlying's price is moved onto the chart.</summary>
    public enum PriceMapping
    {
        /// <summary>Index option on an index/futures chart: shift by the basis, keep the scale.</summary>
        Auto = 0,
        /// <summary>Use the number as-is, times the custom scale factor.</summary>
        Direct = 1,
        /// <summary>Shift every level by (chart price - chain spot), keeping the strikes' spacing.</summary>
        AlignToChart = 2,
        /// <summary>Scale every level by (chart price / chain spot) - for QQQ/SPY levels on a futures chart.</summary>
        Normalize = 3
    }

    public enum DataSourceMode
    {
        CboeDelayed = 0,
        LocalJsonFile = 1
    }
}
