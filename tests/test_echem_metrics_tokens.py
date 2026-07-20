"""Tests for the shared echem quantification and filename tokenizer.

The metric expectations here are synthetic-signal properties rather than frozen
numbers, so they document the intended behaviour instead of merely pinning the
current output. Numerical agreement with the original EChem figure scripts was
verified separately across all 281 recordings in the dataset.
"""

from __future__ import annotations

import unittest

import numpy as np

from services import echem_metrics as metrics
from services import echem_tokens as tokens


def _pulse_train(
    duration_s: float = 12.0,
    fs: float = 2000.0,
    period_s: float = 1.0,
    on_s: float = 0.2,
    amplitude: float = 50.0,
    drift: float = 0.0,
    noise: float = 0.0,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Square photocurrent pulses on an optional drifting baseline."""
    t = np.arange(0.0, duration_s, 1.0 / fs)
    y = np.zeros_like(t)
    for onset in np.arange(1.0, duration_s - 1.0, period_s):
        y[(t >= onset) & (t < onset + on_s)] = amplitude
    y = y + drift * t
    if noise:
        y = y + np.random.default_rng(seed).normal(0.0, noise, t.size)
    return t, y


class PulseDetectionTests(unittest.TestCase):
    def test_detects_periodic_train_and_period(self) -> None:
        t, y = _pulse_train(period_s=1.0)
        detrended = metrics.rolling_median_detrend(y, 1501)
        onsets, dt, period = metrics.detect_pulses(t, detrended, metrics.DEFAULT_DETECTION)

        self.assertGreaterEqual(onsets.size, 8)
        self.assertAlmostEqual(period, 1.0, places=2)
        self.assertAlmostEqual(dt, 5e-4, places=6)

    def test_isolated_spike_is_rejected_as_aperiodic(self) -> None:
        t, y = _pulse_train(period_s=1.0)
        detrended = metrics.rolling_median_detrend(y, 1501)
        _baseline_onsets, _dt, _period = metrics.detect_pulses(
            t, detrended, metrics.DEFAULT_DETECTION
        )

        spiked = detrended.copy()
        spiked[int(0.5 * 2000)] += 500.0  # lone artifact, off the pulse grid
        onsets, _dt, period = metrics.detect_pulses(t, spiked, metrics.DEFAULT_DETECTION)

        self.assertAlmostEqual(period, 1.0, places=2)
        self.assertNotIn(int(0.5 * 2000), onsets.tolist())

    def test_estimate_period_tolerates_missed_pulses(self) -> None:
        # Onsets at 1, 2, 4, 5, 7 s: the 2->4 and 5->7 gaps span two periods.
        onsets = np.array([1.0, 2.0, 4.0, 5.0, 7.0])
        period, links = metrics.estimate_period(onsets, metrics.DEFAULT_DETECTION)

        self.assertAlmostEqual(period, 1.0, places=6)
        self.assertTrue(links.all())

    def test_adc_rails_detects_pinned_converter(self) -> None:
        clean = np.linspace(-1.0, 1.0, 5000)
        self.assertIsNone(metrics.adc_rails(clean))

        pinned = np.clip(np.linspace(-2.0, 2.0, 5000), -1.0, 1.0)
        self.assertIsNotNone(metrics.adc_rails(pinned))


class PulseMetricsTests(unittest.TestCase):
    def test_amplitude_and_charge_match_the_known_pulse(self) -> None:
        amplitude, on_s = 50.0, 0.2
        t, y = _pulse_train(amplitude=amplitude, on_s=on_s, period_s=1.0)
        result = metrics.pulse_metrics(t, y)

        self.assertGreaterEqual(result["amplitudes_nA"].size, 8)
        self.assertAlmostEqual(float(np.median(result["amplitudes_nA"])), amplitude, delta=1.0)
        self.assertEqual(result["polarity"], "anodic")

        # Charge integrates the ON window only; the measurement window is capped
        # at 200 ms, which is exactly the 200 ms pulse here.
        self.assertAlmostEqual(
            float(np.median(result["charge_nC"])), amplitude * on_s, delta=0.15 * amplitude * on_s
        )

    def test_linear_drift_is_removed_before_measurement(self) -> None:
        flat = metrics.pulse_metrics(*_pulse_train(drift=0.0))
        drifting = metrics.pulse_metrics(*_pulse_train(drift=2.0))

        self.assertAlmostEqual(
            float(np.median(flat["amplitudes_nA"])),
            float(np.median(drifting["amplitudes_nA"])),
            delta=1.0,
        )

    def test_cathodic_polarity_is_detected_and_signed(self) -> None:
        # A trace of exactly two repeated values reads as a pinned ADC, so the
        # signed path would reject every pulse; noise makes it a real recording.
        t, y = _pulse_train(amplitude=-50.0, noise=0.5)

        unsigned = metrics.pulse_metrics(t, y, {"polarity": "auto"})
        self.assertEqual(unsigned["polarity"], "cathodic")
        self.assertGreater(float(np.median(unsigned["amplitudes_nA"])), 0.0)

        signed = metrics.pulse_metrics(t, y, signed=True)
        self.assertEqual(signed["polarity"], "cathodic")
        self.assertLess(float(np.median(signed["amplitudes_nA"])), 0.0)

    def test_railed_recording_is_reported_not_silently_measured(self) -> None:
        """A pinned ADC must show up as a QC count, not as quiet data loss."""
        t, y = _pulse_train(amplitude=-50.0)  # exactly two repeated levels
        signed = metrics.pulse_metrics(t, y, signed=True)

        self.assertEqual(signed["amplitudes_nA"].size, 0)
        self.assertGreater(signed["n_clipped"], 0)

    def test_summary_is_json_safe_scalars(self) -> None:
        summary = metrics.pulse_metrics_summary(*_pulse_train())

        self.assertGreater(summary["n_pulses"], 0)
        self.assertAlmostEqual(summary["period_s"], 1.0, places=2)
        self.assertAlmostEqual(summary["frequency_Hz"], 1.0, places=2)
        for key, value in summary.items():
            self.assertIsInstance(value, (int, float, str, type(None)), msg=key)

    def test_summary_on_featureless_trace_reports_no_pulses(self) -> None:
        t = np.arange(0.0, 5.0, 5e-4)
        summary = metrics.pulse_metrics_summary(t, np.zeros_like(t))

        self.assertEqual(summary["n_pulses"], 0)
        self.assertIsNone(summary["period_s"])
        self.assertTrue(np.isnan(summary["amplitude_nA"]))

    def test_summarize_values_reports_spread_not_standard_error(self) -> None:
        values = np.array([1.0, 2.0, 3.0, 4.0])
        median, sd, iqr = metrics.summarize_values(values)

        self.assertAlmostEqual(median, 2.5)
        self.assertAlmostEqual(sd, float(np.std(values, ddof=1)))
        self.assertAlmostEqual(iqr, 1.5)

    def test_average_pulse_returns_baselined_composite(self) -> None:
        t, y = _pulse_train(noise=1.0)
        relative_ms, composite, n_cycles = metrics.average_pulse(t, y, window_ms=(-20.0, 200.0))

        self.assertIsNotNone(relative_ms)
        self.assertGreaterEqual(n_cycles, 8)
        self.assertAlmostEqual(float(np.median(composite[relative_ms < 0])), 0.0, delta=1.0)
        self.assertAlmostEqual(float(np.max(composite)), 50.0, delta=3.0)


def _cp_square_wave(period_s: float = 2.0, duty: float = 0.25, amplitude: float = 8.0):
    t = np.arange(0.0, 20.0, 1e-3)
    return t, np.where((t % period_s) < duty * period_s, amplitude, 0.0)


class CycleAmplitudeTests(unittest.TestCase):
    def test_photovoltage_span_matches_the_square_wave(self) -> None:
        t, potential = _cp_square_wave(duty=0.25, amplitude=8.0)
        amplitudes, period_ms = metrics.cycle_amplitudes(t, potential, {"expected_period_s": 2.0})

        self.assertGreater(amplitudes.size, 3)
        self.assertAlmostEqual(float(np.median(amplitudes)), 8.0, delta=0.5)
        self.assertAlmostEqual(period_ms, 2000.0, places=6)

    def test_inferred_period_collapses_to_the_edge_interval(self) -> None:
        """Without an expected period the light cycle is read as its half.

        Both the ON and the OFF transition are edges, so edge spacing is half
        the cycle. This is why every EChem figure job supplies the period, and
        why the summary marks the inferred case.
        """
        t, potential = _cp_square_wave(period_s=2.0, duty=0.5)
        _amplitudes, period_ms = metrics.cycle_amplitudes(t, potential)

        self.assertAlmostEqual(period_ms, 1000.0, delta=20.0)

    def test_summary_marks_the_period_source(self) -> None:
        t, potential = _cp_square_wave(duty=0.25)

        expected = metrics.cycle_amplitudes_summary(t, potential, {"expected_period_s": 2.0})
        self.assertEqual(expected["period_source"], "expected")
        self.assertAlmostEqual(expected["amplitude_mV"], 8.0, delta=0.5)

        inferred = metrics.cycle_amplitudes_summary(t, potential)
        self.assertEqual(inferred["period_source"], "inferred")

    def test_fifty_percent_duty_inflates_the_reported_swing(self) -> None:
        """Documents a known bias: the drift baseline is unstable at 50% duty.

        A running median taken over one period sits mid-swing for a balanced
        square wave and moves in antiphase with it, so subtracting it roughly
        doubles the measured span. Asymmetric duty cycles are unaffected. This
        is inherited behaviour, pinned here so a future change is deliberate.
        """
        t, balanced = _cp_square_wave(duty=0.5, amplitude=8.0)
        amplitudes, _period_ms = metrics.cycle_amplitudes(t, balanced, {"expected_period_s": 2.0})

        self.assertAlmostEqual(float(np.median(amplitudes)), 16.0, delta=1.0)


class CyclicVoltammetryTests(unittest.TestCase):
    def test_final_cycle_anodic_peak_is_branch_aware(self) -> None:
        up = np.linspace(0.0, 0.5, 101, endpoint=False)
        down = np.linspace(0.5, -0.5, 201, endpoint=False)
        return_branch = np.linspace(-0.5, 0.0, 101)
        one_cycle = np.concatenate([up, down, return_branch])
        potential = np.tile(one_cycle, 3)
        current = np.zeros_like(potential)
        for offset in range(0, len(potential), len(one_cycle)):
            branch = slice(offset + len(up) + len(down), offset + len(one_cycle))
            current[branch] = 4.0 * np.exp(-(((potential[branch] + 0.18) / 0.025) ** 2))

        summary = metrics.cv_anodic_peak_summary(potential, current)

        self.assertTrue(summary["anodic_valid"])
        self.assertEqual(summary["anodic_status"], "resolved")
        self.assertAlmostEqual(summary["Epa_V"], -0.18, delta=0.01)
        self.assertAlmostEqual(summary["Ipa_uA"], 4.0, delta=0.1)


class SquareWaveTests(unittest.TestCase):
    def test_spike_and_plateau_are_separated(self) -> None:
        fs, period, on_s = 5000.0, 0.4925, 0.2462
        t = np.arange(0.0, 30.0, 1.0 / fs)
        phase = np.mod(t, period)
        plateau_level, spike_level = 20.0, 60.0

        current = np.where(phase < on_s, plateau_level, 0.0)
        # Fast capacitive transient on the first 5 ms of every ON edge.
        current = np.where(phase < 0.005, spike_level, current)

        result = metrics.square_wave_metrics(t, current, period_hint_s=period / 0.9855)

        self.assertEqual(result["flag"], "ok")
        self.assertGreater(result["n_cycles"], 20)
        self.assertGreater(result["spike_nA"], result["plateau_nA"])
        self.assertGreater(result["plateau_nA"], 0.0)
        self.assertAlmostEqual(result["period_ms"], period * 1e3, delta=1.0)
        # Absolute scale is set by the drift baseline removed beforehand, so the
        # plateau is compared against spike rather than the raw input level.
        self.assertGreater(result["spike_nA"], spike_level - plateau_level)

    def test_plateau_is_duty_cycle_dependent(self) -> None:
        """Documents why plateau must not be compared across light timings.

        The plateau contrasts the upper and lower 30% of the composite, which
        presumes ON and OFF occupy comparable fractions of a cycle. At 25% duty
        the OFF level dominates both quantiles and the plateau collapses, while
        the identical response at 50% duty reports a large value.
        """
        fs, period, spike_level, plateau_level = 5000.0, 0.4925, 60.0, 20.0
        measured = {}
        for duty in (0.25, 0.5):
            t = np.arange(0.0, 30.0, 1.0 / fs)
            phase = np.mod(t, period)
            current = np.where(phase < duty * period, plateau_level, 0.0)
            current = np.where(phase < 0.005, spike_level, current)
            measured[duty] = metrics.square_wave_metrics(t, current, period_hint_s=period / 0.9855)[
                "plateau_nA"
            ]

        self.assertAlmostEqual(measured[0.25], 0.0, delta=1e-6)
        self.assertGreater(measured[0.5], 10.0)

    def test_unmodulated_trace_is_flagged(self) -> None:
        t = np.arange(0.0, 20.0, 1e-3)
        result = metrics.square_wave_metrics(t, np.zeros_like(t), period_hint_s=0.5)

        self.assertIn(result["flag"], {"no_modulation", "ok"})
        if result["flag"] == "ok":
            self.assertAlmostEqual(result["spike_nA"], 0.0, delta=1e-6)

    def test_overrange_recording_is_flagged(self) -> None:
        t = np.arange(0.0, 10.0, 1e-3)
        result = metrics.square_wave_metrics(t, np.full_like(t, 5e6), period_hint_s=0.5)

        self.assertEqual(result["flag"], "overrange(|I|>1mA)")


class NumberDecodingTests(unittest.TestCase):
    def test_p_is_the_decimal_point(self) -> None:
        self.assertAlmostEqual(tokens.decode_number("0p375"), 0.375)
        self.assertAlmostEqual(tokens.decode_number("5"), 5.0)

    def test_leading_m_and_p_are_signs(self) -> None:
        self.assertAlmostEqual(tokens.decode_number("m0p2"), -0.2)
        self.assertAlmostEqual(tokens.decode_number("p0p3"), 0.3)

    def test_unparseable_input_returns_none(self) -> None:
        self.assertIsNone(tokens.decode_number(""))
        self.assertIsNone(tokens.decode_number("abc"))


class RecordingNameTests(unittest.TestCase):
    def test_scaling_recording(self) -> None:
        parsed = tokens.parse_recording_name(
            "20260707_scaling/mb_5mM_oil_1pct_distance_5cm_parallel_group_01_CA.csv"
        )
        fields = parsed["fields"]

        self.assertEqual(fields["technique"], "CA")
        self.assertEqual(fields["analyte"], "mb")
        self.assertAlmostEqual(fields["concentration_mM"], 5.0)
        self.assertAlmostEqual(fields["oil_pct"], 1.0)
        self.assertAlmostEqual(fields["distance_cm"], 5.0)
        self.assertEqual(fields["replicate"], 1)
        self.assertEqual(fields["replicate_kind"], "parallel_group")
        self.assertEqual(fields["session"], "20260707_scaling")
        self.assertEqual(parsed["unparsed"], [])

    def test_bias_and_light_timing_recording(self) -> None:
        parsed = tokens.parse_recording_name(
            "mb_5mM_oil_1pct_light_on_0p25s_off_0p75s_nominal_bias_m0p2V_parallel_group_01_COR.csv"
        )
        fields = parsed["fields"]

        self.assertEqual(fields["technique"], "COR")
        self.assertAlmostEqual(fields["bias_V"], -0.2)
        self.assertAlmostEqual(fields["light_on_s"], 0.25)
        self.assertAlmostEqual(fields["light_off_s"], 0.75)
        self.assertAlmostEqual(fields["light_period_s"], 1.0)
        self.assertAlmostEqual(fields["light_duty"], 0.25)
        self.assertEqual(fields["sweep_mode"], "discrete_bias")

    def test_micromolar_is_normalized_to_millimolar(self) -> None:
        parsed = tokens.parse_recording_name(
            "mb_solution_50uM_scan_200mVps_parallel_group_01_CV.csv"
        )
        fields = parsed["fields"]

        self.assertAlmostEqual(fields["concentration_mM"], 0.05)
        self.assertAlmostEqual(fields["scan_rate_mVps"], 200.0)
        self.assertEqual(fields["substrate"], "solution")

    def test_nanometre_is_not_read_as_nanomolar(self) -> None:
        """``200nm`` is a film thickness; only a capital ``M`` means molar."""
        parsed = tokens.parse_recording_name("au_thickness_200nm_saline_parallel_group_01_CA.csv")
        fields = parsed["fields"]

        self.assertNotIn("concentration_mM", fields)
        self.assertAlmostEqual(fields["au_thickness_nm"], 200.0)
        self.assertIn("saline", fields["qualifiers"])

    def test_wavelength_is_kept_for_dye_screen(self) -> None:
        parsed = tokens.parse_recording_name("beta_carotene_625nm_parallel_group_01_CA.csv")
        fields = parsed["fields"]

        self.assertEqual(fields["analyte"], "beta_carotene")
        self.assertAlmostEqual(fields["wavelength_nm"], 625.0)

    def test_txt_ph_session_recording(self) -> None:
        parsed = tokens.parse_recording_name(
            "20260716/mb_5mM_oil_1pct_ph_07_light_on_1s_off_1s_ph_scan_02.txt"
        )
        fields = parsed["fields"]

        self.assertEqual(fields["technique"], "COR")
        self.assertAlmostEqual(fields["ph"], 7.0)
        self.assertEqual(fields["replicate"], 2)
        self.assertEqual(fields["replicate_kind"], "scan")

    def test_staircase_is_distinguished_from_discrete_bias(self) -> None:
        staircase = tokens.parse_recording_name("mb_5mM_oil_1pct_bias_staircase_range_25uA_CA.csv")
        self.assertEqual(staircase["fields"]["sweep_mode"], "staircase")
        self.assertAlmostEqual(staircase["fields"]["current_range_uA"], 25.0)

        discrete = tokens.parse_recording_name("mb_5mM_oil_1pct_nominal_bias_p0p3V_pass_01_CA.csv")
        self.assertEqual(discrete["fields"]["sweep_mode"], "discrete_bias")
        self.assertEqual(discrete["fields"]["replicate_kind"], "pass")

    def test_unknown_fragment_is_surfaced_not_swallowed(self) -> None:
        parsed = tokens.parse_recording_name("mystery_dye_570nm_parallel_group_01_CA.csv")

        self.assertIn("mystery", parsed["unparsed"])
        self.assertIn("dye", parsed["unparsed"])

    def test_tokens_follow_the_declared_order(self) -> None:
        parsed = tokens.parse_recording_name(
            "mb_5mM_oil_1pct_distance_5cm_parallel_group_01_CA.csv"
        )
        names = [token.split("=", 1)[0] for token in parsed["tokens"]]
        positions = [tokens.TOKEN_ORDER.index(name) for name in names]

        self.assertEqual(positions, sorted(positions))
        self.assertIn("technique=CA", parsed["tokens"])
        self.assertIn("oil_pct=1", parsed["tokens"])

    def test_label_is_compact_and_human_readable(self) -> None:
        parsed = tokens.parse_recording_name(
            "mb_5mM_oil_1pct_distance_5cm_parallel_group_01_CA.csv"
        )
        self.assertEqual(parsed["label"], "mb 5 mM oil 1% d=5 cm #1")


class TokenFacetTests(unittest.TestCase):
    def test_numeric_facets_sort_numerically_with_counts(self) -> None:
        parsed = tokens.parse_recording_names(
            [
                "mb_10mM_parallel_group_01_CA.csv",
                "mb_0p1mM_parallel_group_01_CA.csv",
                "mb_2mM_parallel_group_01_CA.csv",
                "mb_2mM_parallel_group_02_CA.csv",
            ]
        )
        facets = {facet["token"]: facet for facet in tokens.token_facets(parsed)}
        concentration = facets["concentration_mM"]

        self.assertTrue(concentration["numeric"])
        self.assertEqual([v["value"] for v in concentration["values"]], ["0.1", "2", "10"])
        self.assertEqual([v["count"] for v in concentration["values"]], [1, 2, 1])

    def test_list_valued_qualifiers_expand_into_separate_facet_values(self) -> None:
        parsed = tokens.parse_recording_names(
            [
                "mb_50mM_oil_0p5pct_ultrasound_parallel_group_01_CA.csv",
                "mb_50mM_oil_0p5pct_no_ultrasound_parallel_group_01_CA.csv",
            ]
        )
        facets = {facet["token"]: facet for facet in tokens.token_facets(parsed)}
        values = {v["value"] for v in facets["qualifiers"]["values"]}

        self.assertIn("ultrasound", values)
        self.assertIn("no_ultrasound", values)


if __name__ == "__main__":
    unittest.main()
