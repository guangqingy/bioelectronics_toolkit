from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from services import abf, csv_tools, echem, echem_lineshape, emg, rhd


def _fake_find_peaks(signal, height=None, distance=None, **_kwargs):
    threshold = -np.inf if height is None else float(height)
    indexes = np.where(np.asarray(signal, dtype=float) >= threshold)[0]
    if distance and indexes.size > 1:
        kept = [int(indexes[0])]
        for idx in indexes[1:]:
            if int(idx) - kept[-1] >= int(distance):
                kept.append(int(idx))
        indexes = np.asarray(kept, dtype=int)
    return indexes.astype(int), {"prominences": np.ones(indexes.size, dtype=float)}


def _fake_peak_widths(_signal, peaks, rel_height=0.5):
    widths = np.ones(len(peaks), dtype=float) * 2.0
    return widths, None, None, None


class CsvToolsServiceTests(unittest.TestCase):
    def test_load_window_and_merge_tables(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_csv_service_") as tmp:
            first = Path(tmp) / "a.csv"
            second = Path(tmp) / "b.csv"
            first.write_text("time,value\n0,10\n1,11\n2,12\n", encoding="utf-8")
            second.write_text("time,value\n2,20\n3,21\n", encoding="utf-8")

            self.assertEqual(csv_tools.read_columns(first), ["time", "value"])
            x, y = csv_tools.load_xy(first, "time", "value", x_min=1, x_max=2)

            np.testing.assert_allclose(x, [1, 2])
            np.testing.assert_allclose(y, [11, 12])

            merged = csv_tools.merge_xy_tables([first, second], "time", "value")
            self.assertEqual(merged["time"].tolist(), [0, 1, 2, 3])
            self.assertEqual(csv_tools.default_merge_name(), "merged_preview_auto-auto.csv")


class EchemServiceTests(unittest.TestCase):
    def test_load_photocurrent_sorts_time_column(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_echem_service_") as tmp:
            path = Path(tmp) / "pc.txt"
            path.write_text("Time/s\tI/mA\n2\t0.2\n1\t0.1\n", encoding="utf-8")

            t, signal, t_col, v_col = echem.load_photocurrent(path)

            np.testing.assert_allclose(t, [1.0, 2.0])
            np.testing.assert_allclose(signal, [0.1, 0.2])
            self.assertEqual((t_col, v_col), ("Time/s", "I/mA"))

    def test_detect_photocurrent_pairs(self) -> None:
        t = np.arange(10, dtype=float) * 0.001
        signal = np.zeros_like(t)
        signal[2] = 0.5
        signal[5] = -0.4

        pairs = echem.detect_photocurrent_pairs(
            t,
            signal,
            t0=0.0,
            t1=0.009,
            pos_min_mA=0.2,
            neg_min_abs_mA=0.2,
            min_delay_ms=1.0,
            max_delay_ms=5.0,
            min_pos_distance_ms=1.0,
            find_peaks_func=_fake_find_peaks,
        )

        self.assertEqual(pairs, [(2, 5)])

    def test_pulse_detection_reports_width(self) -> None:
        t = np.arange(10, dtype=float) * 0.001
        detrended = np.zeros_like(t)
        detrended[4] = 1.0

        pulses = echem.detect_positive_pulses(
            t,
            detrended,
            t0=0.0,
            t1=0.009,
            peak_min_v=0.5,
            min_width_ms=1.0,
            min_spacing_ms=1.0,
            find_peaks_func=_fake_find_peaks,
            peak_widths_func=_fake_peak_widths,
        )

        self.assertEqual(pulses[0]["idx"], 4)
        self.assertAlmostEqual(pulses[0]["width_ms"], 2.0)


class EchemLineshapeServiceTests(unittest.TestCase):
    def _write_segment(self, path: Path, values: list[float]) -> None:
        times = [-0.002, -0.001, 0.0, 0.001, 0.002]
        rows = ["time_s,current_mA"]
        rows.extend(f"{t},{v}" for t, v in zip(times, values))
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def test_load_samples_filters_chambers_and_centers_peak(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_lineshape_") as tmp:
            root = Path(tmp)
            keep_dir = root / "ATAT" / "Photocurrent" / "ATAT_1001_3"
            skip_dir = root / "ATAT" / "Photocurrent" / "ATAT_1004_3"
            keep_dir.mkdir(parents=True)
            skip_dir.mkdir(parents=True)
            self._write_segment(keep_dir / "ATAT_1001_3_pair_001.csv", [0, 1, 5, 2, 0])
            self._write_segment(skip_dir / "ATAT_1004_3_pair_001.csv", [0, 1, 4, 2, 0])

            payload = echem_lineshape.load_samples_payload(
                {
                    "base_dir": str(root),
                    "material": "ATAT",
                    "index_k": 3,
                    "kind": "photocurrent",
                    "chambers": "1,2,3",
                }
            )

            self.assertEqual(payload["n"], 1)
            sample = payload["samples"][0]
            self.assertEqual(sample["device"], "ATAT_1001_3")
            peak_index = int(np.argmax(sample["y"]))
            self.assertAlmostEqual(sample["t"][peak_index], 0.0)

    def test_load_samples_from_source_file_reads_same_stem_pair_folder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_lineshape_source_") as tmp:
            root = Path(tmp)
            source = root / "ATAT" / "Photocurrent" / "ATAT_photocurrent_1_1.txt"
            pair_dir = source.with_suffix("")
            pair_dir.mkdir(parents=True)
            source.write_text("time_s,current_mA\n0,0\n", encoding="utf-8")
            self._write_segment(
                pair_dir / "ATAT_photocurrent_1_1_pair_001.csv",
                [0, 1, 5, 2, 0],
            )
            self._write_segment(
                pair_dir / "ATAT_photocurrent_1_1_pair_002.csv",
                [0, 1, 4, 2, 0],
            )

            payload = echem_lineshape.load_samples_payload(
                {
                    "source_path": str(source),
                    "kind": "photocurrent",
                }
            )

            self.assertEqual(payload["n"], 2)
            self.assertEqual(payload["kind"], "photocurrent")
            self.assertEqual(payload["source_path"], str(source))
            self.assertTrue(payload["segment_dir"].endswith("ATAT_photocurrent_1_1"))

    def test_average_and_export_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_lineshape_export_") as tmp:
            root = Path(tmp)
            samples = [
                {"label": "a", "t": [-0.001, 0.0, 0.001], "y": [0.0, 2.0, 0.0]},
                {"label": "b", "t": [-0.001, 0.0, 0.001], "y": [0.0, 4.0, 0.0]},
            ]
            grid, avg = echem_lineshape.compute_average(
                samples,
                [0, 1],
                x_min=-0.001,
                x_max=0.001,
                x_offset=0.0,
            )
            self.assertAlmostEqual(float(avg[np.argmin(np.abs(grid))]), 3.0)

            result = echem_lineshape.export_average_files(
                {
                    "base_dir": str(root),
                    "material": "ATAT",
                    "index_k": 1,
                    "kind": "photocurrent",
                    "avg_data": {"time_s": grid.tolist(), "y": avg.tolist()},
                    "crop_t0": -0.001,
                    "crop_t1": 0.001,
                }
            )

            self.assertTrue(Path(result["csv_path"]).exists())
            self.assertTrue(Path(result["png_path"]).exists())
            self.assertTrue(Path(result["svg_path"]).exists())
            self.assertEqual(len(result["outputs"]), 3)

    def test_source_export_defaults_to_project_plots_folder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_lineshape_source_export_") as tmp:
            source = Path(tmp) / "DateRun" / "ATAT" / "Photocurrent" / "ATAT_photocurrent_1_1.txt"
            source.parent.mkdir(parents=True)
            source.write_text("time_s,current_mA\n0,0\n", encoding="utf-8")

            result = echem_lineshape.export_average_files(
                {
                    "source_path": str(source),
                    "kind": "photocurrent",
                    "avg_data": {"time_s": [-0.001, 0.0, 0.001], "y": [0.0, 1.0, 0.0]},
                    "crop_t0": -0.001,
                    "crop_t1": 0.001,
                }
            )

            self.assertEqual(Path(result["output_dir"]), Path(tmp) / "DateRun" / "plots_shape_average")
            self.assertTrue(Path(result["csv_path"]).name.startswith("shape_ATAT_photocurrent_1_1"))


class AbfServiceTests(unittest.TestCase):
    def test_baseline_apply_uses_requested_window(self) -> None:
        t = np.arange(5, dtype=float) * 0.001
        y = np.asarray([5.0, 5.0, 7.0, 8.0, 9.0])

        corrected, baseline = abf.baseline_apply(y, t, pre0_ms=0.0, pre1_ms=2.0)

        self.assertAlmostEqual(baseline, 5.0)
        np.testing.assert_allclose(corrected, [0, 0, 2, 3, 4])

    def test_detect_peaks_returns_global_indexes(self) -> None:
        t = np.arange(8, dtype=float) * 0.001
        y = np.asarray([0, 0, 2, 0, 0, 3, 0, 0], dtype=float)

        peaks, window = abf.detect_peaks(
            t,
            y,
            t0=None,
            t1=None,
            use_all=True,
            polarity="positive",
            distance_ms=1.0,
            height=1.0,
            find_peaks_func=_fake_find_peaks,
        )

        self.assertEqual([row["global_index"] for row in peaks], [2, 5])
        self.assertEqual(window, [0.0, 0.007])


class EmgServiceTests(unittest.TestCase):
    def test_numeric_signal_and_sampling_rate(self) -> None:
        df = pd.DataFrame(
            {
                "Time_s": [0.0, 0.001, "bad", 0.002],
                "Value_uV": [0.0, 1.0, 2.0, 3.0],
            }
        )
        t_col, v_col = emg.pick_columns(df)
        t_raw, v_raw, valid = emg.numeric_signal(df, t_col, v_col)

        np.testing.assert_allclose(t_raw[valid], [0.0, 0.001, 0.002])
        np.testing.assert_allclose(v_raw[valid], [0.0, 1.0, 3.0])
        self.assertAlmostEqual(emg.infer_sampling_rate(t_raw[valid]), 1000.0)

    def test_detect_with_polarity_combines_positive_and_negative(self) -> None:
        signal = np.asarray([0.0, 0.0, 3.0, 0.0, -4.0, 0.0])
        params = {
            "min_peak_distance_ms": 1.0,
            "min_width_ms": None,
            "wlen_ms": None,
            "min_prominence_uV": None,
            "min_height_uV": 1.0,
            "use_adaptive_sigma": False,
            "sigma_for_prom": 1.0,
            "sigma_for_height": 1.0,
        }

        peaks, _widths, signs = emg.detect_with_polarity(
            signal,
            fs=1000.0,
            params=params,
            polarity="both",
            find_peaks_func=_fake_find_peaks,
            peak_widths_func=_fake_peak_widths,
        )

        self.assertEqual(peaks.tolist(), [2, 4])
        self.assertEqual(signs.tolist(), [1, -1])


class _FakeRhdModule:
    @staticmethod
    def load_file(path: str):
        suffix = Path(path).stem[-4:]
        data = np.asarray([[1.0, 2.0], [3.0, 4.0]])
        if suffix == "0100":
            data = np.asarray([[5.0, 6.0], [7.0, 8.0]])
        elif suffix == "0200":
            data = np.asarray([[9.0, 10.0], [11.0, 12.0]])
        return (
            {
                "frequency_parameters": {"amplifier_sample_rate": 1000.0},
                "t_amplifier": np.asarray([0.0, 0.001]),
                "amplifier_data": data,
                "amplifier_channels": [
                    {"custom_channel_name": "A", "native_channel_name": "native-A"},
                    {"custom_channel_name": "B", "native_channel_name": "native-B"},
                ],
            },
            {},
        )


class RhdServiceTests(unittest.TestCase):
    def test_load_merged_pair_and_wide_frame(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_rhd_service_") as tmp:
            first = Path(tmp) / "sample_0000.rhd"
            second = Path(tmp) / "sample_0100.rhd"
            first.write_text("", encoding="utf-8")
            second.write_text("", encoding="utf-8")

            t, fs, names, amp, base_stem, used_pair = rhd.load_merged_if_pair(
                first,
                _FakeRhdModule,
            )
            wide = rhd.all_channels_wide_frame(t, names, amp)

            self.assertTrue(used_pair)
            self.assertEqual(base_stem, "sample_0000")
            self.assertAlmostEqual(fs, 1000.0)
            np.testing.assert_allclose(t, [0.0, 0.001, 0.002, 0.003])
            self.assertEqual(wide.columns.tolist(), ["time", "A", "B"])
            self.assertEqual(wide["A"].tolist(), [1.0, 2.0, 5.0, 6.0])

    def test_load_with_merge_option_merges_whole_folder_recording(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_rhd_folder_service_") as tmp:
            root = Path(tmp)
            for suffix in ("0000", "0100", "0200"):
                (root / f"sample_{suffix}.rhd").write_text("", encoding="utf-8")

            t, fs, names, amp, base_stem, used_merge = rhd.load_with_merge_option(
                root / "sample_0100.rhd",
                _FakeRhdModule,
                True,
            )

            self.assertTrue(used_merge)
            self.assertEqual(base_stem, "sample_0000")
            self.assertAlmostEqual(fs, 1000.0)
            self.assertEqual(names, ["A", "B"])
            np.testing.assert_allclose(t, [0.0, 0.001, 0.002, 0.003, 0.004, 0.005])
            np.testing.assert_allclose(amp[0], [1.0, 2.0, 5.0, 6.0, 9.0, 10.0])

    def test_resolve_channel_by_display_and_native_name(self) -> None:
        result, _ = _FakeRhdModule.load_file("sample_0000.rhd")

        self.assertEqual(rhd.resolve_channel_index(result, "B"), 1)
        self.assertEqual(rhd.resolve_channel_index(result, "native-A"), 0)
        self.assertEqual(rhd.resolve_channel_index(result, "missing", default=3), 3)


if __name__ == "__main__":
    unittest.main()
