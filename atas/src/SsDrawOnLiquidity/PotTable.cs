/*
 * PotTable - fitted probability-of-touch table. GENERATED, do not hand-edit.
 *
 * Source : 1,460 MNQ trading days (2019-07 .. 2026-09), 4,193,607 observations.
 * Label  : target touched BEFORE the stop is hit, same-bar ambiguity scored as
 *          a loss (conservative).
 * Fit    : empirical frequency per cell; cells with n < 120 fall back to the
 *          time-pooled rate, then the R:R-pooled rate.
 *
 * Levels are only counted while strictly unswept, and initial-balance levels
 * are only admitted after the IB window has closed, so no cell depends on a
 * price range that had not finished forming.
 *
 * Indexing: Pot[group, stop, rrBucket, timeBucket]
 *   group      : 0 session_hl  1 sess_extreme  2 pdc  3 swing  4 fvg
 *   stop       : 0 = 20pt  1 = 40pt  2 = 60pt
 *   rrBucket   : <0.5, 0.5-1, 1-1.5, 1.5-2, 2-3, 3-4, 4-6, 6+
 *   timeBucket : 0 = first 90 min of RTH, 1 = 90-210, 2 = last 90
 */
namespace ATAS.Indicators.Technical;

using System;

internal static class PotTable
{
	internal static readonly string[] Groups =
		{ "session_hl", "sess_extreme", "pdc", "swing", "fvg" };

	internal static readonly decimal[] StopPoints = { 20m, 40m, 60m };

	internal static readonly decimal[] RrEdges =
		{ 0.5m, 1.0m, 1.5m, 2.0m, 3.0m, 4.0m, 6.0m };

	internal static int GroupIndex(string group)
	{
		for (var i = 0; i < Groups.Length; i++)
			if (Groups[i] == group)
				return i;
		return -1;
	}

	/// <summary>Nearest fitted stop distance; the table only knows three.</summary>
	internal static int StopIndex(decimal stopPoints)
	{
		var best = 0;
		var bestD = decimal.MaxValue;
		for (var i = 0; i < StopPoints.Length; i++)
		{
			var d = Math.Abs(StopPoints[i] - stopPoints);
			if (d < bestD) { bestD = d; best = i; }
		}
		return best;
	}

	internal static int RrIndex(decimal rr)
	{
		for (var i = 0; i < RrEdges.Length; i++)
			if (rr < RrEdges[i])
				return i;
		return RrEdges.Length;
	}

	internal static readonly float[,,,] Pot = new float[5, 3, 8, 3]
	{
		{ // 0 session_hl
			{ // stop 20
				{ 0.6987f, 0.7607f, 0.7322f }, // rr <0.5
				{ 0.5254f, 0.4812f, 0.5195f }, // rr 0.5-1
				{ 0.4052f, 0.3795f, 0.3751f }, // rr 1-1.5
				{ 0.3532f, 0.3356f, 0.3020f }, // rr 1.5-2
				{ 0.2737f, 0.2266f, 0.2155f }, // rr 2-3
				{ 0.2164f, 0.1661f, 0.1092f }, // rr 3-4
				{ 0.1524f, 0.1077f, 0.0569f }, // rr 4-6
				{ 0.0755f, 0.0409f, 0.0154f }, // rr 6+
			},
			{ // stop 40
				{ 0.7045f, 0.6524f, 0.6533f }, // rr <0.5
				{ 0.5381f, 0.5036f, 0.4159f }, // rr 0.5-1
				{ 0.4140f, 0.3271f, 0.2622f }, // rr 1-1.5
				{ 0.3375f, 0.2435f, 0.1363f }, // rr 1.5-2
				{ 0.2410f, 0.1666f, 0.0781f }, // rr 2-3
				{ 0.1681f, 0.1052f, 0.0426f }, // rr 3-4
				{ 0.1098f, 0.0517f, 0.0184f }, // rr 4-6
				{ 0.0593f, 0.0321f, 0.0090f }, // rr 6+
			},
			{ // stop 60
				{ 0.7019f, 0.6425f, 0.5796f }, // rr <0.5
				{ 0.5160f, 0.4191f, 0.3178f }, // rr 0.5-1
				{ 0.3844f, 0.2658f, 0.1392f }, // rr 1-1.5
				{ 0.2777f, 0.1912f, 0.0881f }, // rr 1.5-2
				{ 0.1961f, 0.1078f, 0.0419f }, // rr 2-3
				{ 0.1219f, 0.0557f, 0.0183f }, // rr 3-4
				{ 0.0764f, 0.0389f, 0.0099f }, // rr 4-6
				{ 0.1916f, 0.1916f, 0.1916f }, // rr 6+
			},
		},
		{ // 1 sess_extreme
			{ // stop 20
				{ 0.7494f, 0.7538f, 0.7945f }, // rr <0.5
				{ 0.5570f, 0.5448f, 0.5676f }, // rr 0.5-1
				{ 0.4472f, 0.4334f, 0.4143f }, // rr 1-1.5
				{ 0.3749f, 0.3363f, 0.3114f }, // rr 1.5-2
				{ 0.2812f, 0.2654f, 0.2193f }, // rr 2-3
				{ 0.2070f, 0.1911f, 0.1400f }, // rr 3-4
				{ 0.1561f, 0.1348f, 0.0752f }, // rr 4-6
				{ 0.1000f, 0.0633f, 0.0263f }, // rr 6+
			},
			{ // stop 40
				{ 0.7796f, 0.7621f, 0.7791f }, // rr <0.5
				{ 0.5779f, 0.5321f, 0.4803f }, // rr 0.5-1
				{ 0.4141f, 0.3913f, 0.3056f }, // rr 1-1.5
				{ 0.3249f, 0.3013f, 0.1890f }, // rr 1.5-2
				{ 0.2546f, 0.2060f, 0.1074f }, // rr 2-3
				{ 0.1937f, 0.1302f, 0.0571f }, // rr 3-4
				{ 0.1407f, 0.0847f, 0.0309f }, // rr 4-6
				{ 0.0880f, 0.0465f, 0.0119f }, // rr 6+
			},
			{ // stop 60
				{ 0.7857f, 0.7553f, 0.7284f }, // rr <0.5
				{ 0.5452f, 0.4923f, 0.3847f }, // rr 0.5-1
				{ 0.3941f, 0.3312f, 0.1984f }, // rr 1-1.5
				{ 0.2969f, 0.2286f, 0.1075f }, // rr 1.5-2
				{ 0.2314f, 0.1446f, 0.0600f }, // rr 2-3
				{ 0.1793f, 0.0946f, 0.0287f }, // rr 3-4
				{ 0.1105f, 0.0553f, 0.0158f }, // rr 4-6
				{ 0.3782f, 0.3782f, 0.3782f }, // rr 6+
			},
		},
		{ // 2 pdc
			{ // stop 20
				{ 0.6828f, 0.6099f, 0.6099f }, // rr <0.5
				{ 0.5292f, 0.4249f, 0.3430f }, // rr 0.5-1
				{ 0.4714f, 0.3896f, 0.3759f }, // rr 1-1.5
				{ 0.3486f, 0.3090f, 0.3577f }, // rr 1.5-2
				{ 0.2631f, 0.2713f, 0.2593f }, // rr 2-3
				{ 0.2047f, 0.1864f, 0.0738f }, // rr 3-4
				{ 0.1695f, 0.1183f, 0.0498f }, // rr 4-6
				{ 0.0791f, 0.0467f, 0.0196f }, // rr 6+
			},
			{ // stop 40
				{ 0.7209f, 0.6309f, 0.4564f }, // rr <0.5
				{ 0.5670f, 0.5052f, 0.4448f }, // rr 0.5-1
				{ 0.4002f, 0.3545f, 0.3039f }, // rr 1-1.5
				{ 0.3329f, 0.2627f, 0.1077f }, // rr 1.5-2
				{ 0.2703f, 0.1886f, 0.0820f }, // rr 2-3
				{ 0.1921f, 0.1152f, 0.0380f }, // rr 3-4
				{ 0.1072f, 0.0561f, 0.0372f }, // rr 4-6
				{ 0.0651f, 0.0564f, 0.0108f }, // rr 6+
			},
			{ // stop 60
				{ 0.7332f, 0.6469f, 0.4984f }, // rr <0.5
				{ 0.4952f, 0.4274f, 0.3595f }, // rr 0.5-1
				{ 0.3909f, 0.2949f, 0.1302f }, // rr 1-1.5
				{ 0.3318f, 0.2226f, 0.0798f }, // rr 1.5-2
				{ 0.2183f, 0.1180f, 0.0487f }, // rr 2-3
				{ 0.1177f, 0.0706f, 0.0401f }, // rr 3-4
				{ 0.0811f, 0.0690f, 0.0116f }, // rr 4-6
				{ 0.2022f, 0.2022f, 0.2022f }, // rr 6+
			},
		},
		{ // 3 swing
			{ // stop 20
				{ 0.7318f, 0.7521f, 0.7555f }, // rr <0.5
				{ 0.5560f, 0.5692f, 0.5702f }, // rr 0.5-1
				{ 0.4415f, 0.4486f, 0.4400f }, // rr 1-1.5
				{ 0.3505f, 0.3618f, 0.3596f }, // rr 1.5-2
				{ 0.2872f, 0.2964f, 0.2826f }, // rr 2-3
				{ 0.2207f, 0.2248f, 0.2137f }, // rr 3-4
				{ 0.1667f, 0.1752f, 0.1613f }, // rr 4-6
				{ 0.1148f, 0.1083f, 0.0872f }, // rr 6+
			},
			{ // stop 40
				{ 0.7579f, 0.7804f, 0.7684f }, // rr <0.5
				{ 0.5674f, 0.5832f, 0.5514f }, // rr 0.5-1
				{ 0.4383f, 0.4494f, 0.4106f }, // rr 1-1.5
				{ 0.3528f, 0.3590f, 0.3120f }, // rr 1.5-2
				{ 0.2739f, 0.2732f, 0.2504f }, // rr 2-3
				{ 0.2364f, 0.2070f, 0.1575f }, // rr 3-4
				{ 0.1628f, 0.1349f, 0.1138f }, // rr 4-6
				{ 0.0956f, 0.0956f, 0.0956f }, // rr 6+
			},
			{ // stop 60
				{ 0.7706f, 0.7925f, 0.7627f }, // rr <0.5
				{ 0.5630f, 0.5757f, 0.5139f }, // rr 0.5-1
				{ 0.4287f, 0.4281f, 0.3653f }, // rr 1-1.5
				{ 0.3269f, 0.3298f, 0.2819f }, // rr 1.5-2
				{ 0.3036f, 0.2658f, 0.1986f }, // rr 2-3
				{ 0.2005f, 0.1423f, 0.1200f }, // rr 3-4
				{ 0.1195f, 0.1195f, 0.1195f }, // rr 4-6
				{ 0.6813f, 0.6813f, 0.6813f }, // rr 6+
			},
		},
		{ // 4 fvg
			{ // stop 20
				{ 0.7499f, 0.7857f, 0.7848f }, // rr <0.5
				{ 0.5608f, 0.5634f, 0.5666f }, // rr 0.5-1
				{ 0.4405f, 0.4428f, 0.4295f }, // rr 1-1.5
				{ 0.3627f, 0.3641f, 0.3519f }, // rr 1.5-2
				{ 0.2861f, 0.2899f, 0.2507f }, // rr 2-3
				{ 0.2358f, 0.2299f, 0.1760f }, // rr 3-4
				{ 0.1789f, 0.1742f, 0.1510f }, // rr 4-6
				{ 0.1324f, 0.0891f, 0.0867f }, // rr 6+
			},
			{ // stop 40
				{ 0.7848f, 0.8022f, 0.7922f }, // rr <0.5
				{ 0.5759f, 0.5714f, 0.5286f }, // rr 0.5-1
				{ 0.4364f, 0.4330f, 0.3520f }, // rr 1-1.5
				{ 0.3885f, 0.3530f, 0.2714f }, // rr 1.5-2
				{ 0.2774f, 0.2878f, 0.2199f }, // rr 2-3
				{ 0.2486f, 0.1956f, 0.1414f }, // rr 3-4
				{ 0.1379f, 0.1496f, 0.1537f }, // rr 4-6
				{ 0.0708f, 0.0708f, 0.0640f }, // rr 6+
			},
			{ // stop 60
				{ 0.8056f, 0.8120f, 0.7784f }, // rr <0.5
				{ 0.5753f, 0.5480f, 0.4592f }, // rr 0.5-1
				{ 0.4442f, 0.4220f, 0.3140f }, // rr 1-1.5
				{ 0.3389f, 0.3433f, 0.2291f }, // rr 1.5-2
				{ 0.2819f, 0.2399f, 0.1897f }, // rr 2-3
				{ 0.1694f, 0.1740f, 0.1707f }, // rr 3-4
				{ 0.0943f, 0.0943f, 0.0880f }, // rr 4-6
				{ 0.6880f, 0.6880f, 0.6880f }, // rr 6+
			},
		},
	};
}
