from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from services import abf, abf_batch, csv_tools, echem, echem_lineshape, emg, rhd
from services.emg_peaks import EmgPeaksService


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


class AbfBatchServiceTests(unittest.TestCase):
    def test_scan_filename_tokens_reports_ambiguous_mains_without_guessing(self) -> None:
        payload = abf_batch.scan_filename_tokens(
            [
                "1mm_MOS_sample_2_3_0003.abf",
                "1mm_MOS_sample_2_3_0004.abf",
                "3mm_MOS_sample_1_1_0000.abf",
            ]
        )

        self.assertEqual(payload["mains"], ["1mm", "3mm"])
        self.assertEqual(payload["treats"], ["MOS"])
        self.assertEqual(payload["main_token"], "1mm, 3mm")
        self.assertEqual(payload["treat_token"], "MOS")
        self.assertTrue(payload["multiple_main_tokens"])
        self.assertFalse(payload["multiple_treat_tokens"])
        self.assertEqual(payload["main_counts"], {"1mm": 2, "3mm": 1})

    def test_scan_filename_tokens_accepts_names_without_sample_label(self) -> None:
        payload = abf_batch.scan_filename_tokens(
            [
                "1mm_MOS_1_1_0000.abf",
                "3mm_MOS_2_2_0001.abf",
            ]
        )

        self.assertEqual(payload["main_token"], "1mm, 3mm")
        self.assertEqual(payload["treat_token"], "MOS")
        self.assertEqual(payload["main_counts"], {"1mm": 1, "3mm": 1})

    def test_process_payload_reports_abf_analysis_failures_as_warnings(self) -> None:
        class FakePyabf:
            class ABF:
                def __init__(self, _path: str) -> None:
                    raise RuntimeError("fake read failure")

        with tempfile.TemporaryDirectory(prefix="dataprocess_abf_batch_warning_") as tmp:
            root = Path(tmp)
            (root / "ctrl_T1_sample_1_A_0001.abf").write_bytes(b"not a real abf")

            payload = abf_batch.process_payload(
                {
                    "folder": str(root),
                    "main": "ctrl",
                    "treat": "T1",
                    "powers": "0, 1",
                    "move_files": False,
                    "dry_run": False,
                },
                has_abf=True,
                pyabf_mod=FakePyabf,
                float_or=lambda value, _default: float(value),
                int_or=lambda value, _default: int(value),
                root_dir=root,
            )

        self.assertEqual(payload["n"], 0)
        self.assertTrue(payload["warnings"])
        self.assertIn("Analysis failed for ctrl_T1_sample_1_A_0001.abf", payload["warnings"][0])

    def test_process_payload_writes_legacy_segment_csv_and_summary(self) -> None:
        time_s = np.arange(5000, dtype=float) * 0.001
        current = np.zeros_like(time_s)
        current[2300] = 10.0
        voltage = np.zeros_like(time_s)
        voltage[1800:2500] = -5.0
        analog = np.zeros_like(time_s)
        analog[2200:2600] = 1.0

        class FakePyabf:
            class ABF:
                sweepList = [0]

                def __init__(self, _path: str) -> None:
                    self._channel = 0
                    self.sweepX = time_s

                def setSweep(self, _sweep: int, channel: int = 0) -> None:
                    self._channel = int(channel)
                    self.sweepX = time_s
                    if self._channel == 0:
                        self.sweepY = current
                    elif self._channel == 1:
                        self.sweepY = voltage
                    else:
                        self.sweepY = analog

        with tempfile.TemporaryDirectory(prefix="dataprocess_abf_batch_segments_") as tmp:
            root = Path(tmp)
            source = root / "ctrl_T1_sample_1_A_0001.abf"
            source.write_bytes(b"fake")

            payload = abf_batch.process_payload(
                {
                    "folder": str(root),
                    "main": "ctrl",
                    "treat": "T1",
                    "powers": "",
                    "move_files": False,
                    "dry_run": False,
                    "segment_mode": "auto",
                },
                has_abf=True,
                pyabf_mod=FakePyabf,
                float_or=lambda value, _default: float(value),
                int_or=lambda value, _default: int(value),
                root_dir=root,
            )

            summary = Path(payload["csv_path"])
            segment = root / "ctrl_T1_sample_1_A_0001_segment.csv"
            self.assertTrue(summary.exists())
            self.assertTrue(segment.exists())
            df = pd.read_csv(summary)
            self.assertIn("power_mW", df.columns)
            self.assertTrue(pd.isna(df.loc[0, "power_mW"]))
            self.assertEqual(payload["results"][0]["segment_csv"], str(segment))
            self.assertTrue(any(item["role"] == "abf_batch_segment" for item in payload["outputs"]))

    def test_process_payload_pure_csv_uses_legacy_manual_segment_window(self) -> None:
        time_s = np.arange(5000, dtype=float) * 0.001
        current = np.zeros_like(time_s)
        current[2300] = 10.0
        voltage = np.zeros_like(time_s)
        voltage[1800:2500] = -5.0
        analog = np.zeros_like(time_s)
        analog[2200:2600] = 1.0

        class FakePyabf:
            class ABF:
                sweepList = [0]

                def __init__(self, _path: str) -> None:
                    self.sweepX = time_s

                def setSweep(self, _sweep: int, channel: int = 0) -> None:
                    self.sweepX = time_s
                    if int(channel) == 0:
                        self.sweepY = current
                    elif int(channel) == 1:
                        self.sweepY = voltage
                    else:
                        self.sweepY = analog

        with tempfile.TemporaryDirectory(prefix="dataprocess_abf_batch_pure_csv_") as tmp:
            root = Path(tmp)
            source = root / "ctrl_T1_sample_1_A_0001.abf"
            source.write_bytes(b"fake")

            payload = abf_batch.process_payload(
                {
                    "folder": str(root),
                    "main": "ctrl",
                    "treat": "T1",
                    "powers": "not a number",
                    "move_files": False,
                    "dry_run": False,
                    "segment_mode": "auto",
                    "segment_t0": 0.1,
                    "segment_t1": 0.7,
                    "pure_csv": True,
                },
                has_abf=True,
                pyabf_mod=FakePyabf,
                float_or=lambda value, _default: float(value),
                int_or=lambda value, _default: int(value),
                root_dir=root,
            )

            segment = root / "ctrl_T1_sample_1_A_0001_segment.csv"
            self.assertEqual(payload["n"], 1)
            self.assertTrue(payload["pure_csv"])
            self.assertTrue(segment.exists())
            self.assertFalse((root / "ctrl_T1" / "summary_ctrl_T1.csv").exists())
            df = pd.read_csv(segment)
            self.assertAlmostEqual(float(df["time_s"].iloc[0]), 0.1)
            self.assertAlmostEqual(float(df["time_s"].iloc[-1]), 0.7, delta=0.001)
            self.assertEqual(payload["segment_csv_paths"], [str(segment)])

    def test_process_payload_accepts_legacy_comma_separated_token_lists(self) -> None:
        time_s = np.arange(5000, dtype=float) * 0.001
        current = np.zeros_like(time_s)
        current[2300] = 10.0
        voltage = np.zeros_like(time_s)
        voltage[1800:2500] = -5.0
        analog = np.zeros_like(time_s)
        analog[2200:2600] = 1.0

        class FakePyabf:
            class ABF:
                sweepList = [0]

                def __init__(self, _path: str) -> None:
                    self.sweepX = time_s

                def setSweep(self, _sweep: int, channel: int = 0) -> None:
                    self.sweepX = time_s
                    if int(channel) == 0:
                        self.sweepY = current
                    elif int(channel) == 1:
                        self.sweepY = voltage
                    else:
                        self.sweepY = analog

        with tempfile.TemporaryDirectory(prefix="dataprocess_abf_batch_token_list_") as tmp:
            root = Path(tmp)
            (root / "1mm_MOS_sample_1_1_0000.abf").write_bytes(b"fake")
            (root / "3mm_MOS_sample_1_1_0000.abf").write_bytes(b"fake")

            payload = abf_batch.process_payload(
                {
                    "folder": str(root),
                    "main": "1mm, 3mm",
                    "treat": "MOS",
                    "powers": "",
                    "move_files": False,
                    "dry_run": False,
                    "segment_mode": "auto",
                },
                has_abf=True,
                pyabf_mod=FakePyabf,
                float_or=lambda value, _default: float(value),
                int_or=lambda value, _default: int(value),
                root_dir=root,
            )

            self.assertEqual(payload["n"], 2)
            self.assertEqual(
                set(payload["summary_paths"]),
                {
                    str(root / "1mm_MOS" / "summary_1mm_MOS.csv"),
                    str(root / "3mm_MOS" / "summary_3mm_MOS.csv"),
                },
            )
            self.assertTrue(all(Path(path).exists() for path in payload["summary_paths"]))

    def test_process_payload_accepts_filenames_without_sample_label(self) -> None:
        time_s = np.arange(5000, dtype=float) * 0.001
        current = np.zeros_like(time_s)
        current[2300] = 10.0
        voltage = np.zeros_like(time_s)
        voltage[1800:2500] = -5.0
        analog = np.zeros_like(time_s)
        analog[2200:2600] = 1.0

        class FakePyabf:
            class ABF:
                sweepList = [0]

                def __init__(self, _path: str) -> None:
                    self.sweepX = time_s

                def setSweep(self, _sweep: int, channel: int = 0) -> None:
                    self.sweepX = time_s
                    if int(channel) == 0:
                        self.sweepY = current
                    elif int(channel) == 1:
                        self.sweepY = voltage
                    else:
                        self.sweepY = analog

        with tempfile.TemporaryDirectory(prefix="dataprocess_abf_batch_no_label_") as tmp:
            root = Path(tmp)
            source = root / "3mm_MOS_2_2_0001.abf"
            source.write_bytes(b"fake")

            payload = abf_batch.process_payload(
                {
                    "folder": str(root),
                    "main": "3mm",
                    "treat": "MOS",
                    "powers": "0, 1.25",
                    "move_files": False,
                    "dry_run": False,
                    "segment_mode": "auto",
                },
                has_abf=True,
                pyabf_mod=FakePyabf,
                float_or=lambda value, _default: float(value),
                int_or=lambda value, _default: int(value),
                root_dir=root,
            )

            self.assertEqual(payload["n"], 1)
            self.assertEqual(payload["csv_path"], str(root / "3mm_MOS" / "summary_3mm_MOS.csv"))
            self.assertTrue((root / "3mm_MOS" / "summary_3mm_MOS.csv").exists())
            self.assertTrue((root / "3mm_MOS_2_2_0001_segment.csv").exists())


class EchemServiceTests(unittest.TestCase):
    def test_rolling_median_matches_legacy_edge_padded_window(self) -> None:
        values = np.asarray([0.0, 3.0, 1.0, 9.0, 2.0, 4.0, 5.0], dtype=float)
        win_pts = 5
        pad = win_pts // 2
        padded = np.pad(values, pad, mode="edge")
        expected = np.asarray([np.median(padded[i : i + win_pts]) for i in range(len(values))])

        np.testing.assert_allclose(echem.rolling_median(values, win_pts), expected)

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

    def test_photocurrent_trace_data_payload_decimates_window(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_echem_trace_") as tmp:
            path = Path(tmp) / "pc.csv"
            rows = ["time_s,current_mA"]
            rows.extend(f"{i * 0.001},{float(i)}" for i in range(20))
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            payload = echem.photocurrent_trace_data_payload(
                {"path": str(path), "x_min": 0.005, "x_max": 0.015},
                max_points=6,
            )

        self.assertEqual(payload["x_label"], "time_s")
        self.assertEqual(payload["y_label"], "current_mA")
        self.assertEqual(payload["n_full"], 11)
        self.assertLessEqual(payload["n_points"], 6)
        self.assertTrue(payload["decimated"])


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

    def test_source_file_browse_and_multi_source_load(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_lineshape_multi_") as tmp:
            folder = Path(tmp) / "ATAT" / "Photocurrent"
            sources = [
                folder / "ATAT_photocurrent_1_1.txt",
                folder / "ATAT_photocurrent_2_1.txt",
            ]
            for offset, source in enumerate(sources):
                pair_dir = source.with_suffix("")
                pair_dir.mkdir(parents=True)
                source.write_text("time_s,current_mA\n0,0\n", encoding="utf-8")
                self._write_segment(
                    pair_dir / f"{source.stem}_pair_001.csv",
                    [0, 1, 5 + offset, 2, 0],
                )
                self._write_segment(
                    pair_dir / f"{source.stem}_pair_002.csv",
                    [0, 1, 4 + offset, 2, 0],
                )

            files = echem_lineshape.list_source_files(folder, "photocurrent")
            self.assertEqual([item["name"] for item in files], [source.name for source in sources])
            self.assertEqual([item["segment_count"] for item in files], [2, 2])

            payload = echem_lineshape.load_samples_payload(
                {
                    "source_paths": [str(source) for source in sources],
                    "kind": "photocurrent",
                }
            )

            self.assertEqual(payload["n_sources"], 2)
            self.assertEqual(payload["n"], 4)
            self.assertEqual(payload["source_paths"], [str(source) for source in sources])

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
                    "selected_count": 2,
                    "selected_segments": [
                        {
                            "selected_order": 1,
                            "sample_index": 0,
                            "label": "a",
                            "device": "ATAT_1_1",
                            "source": str(root / "ATAT_photocurrent_1_1.txt"),
                            "file": str(root / "ATAT_photocurrent_1_1_pair_001.csv"),
                        },
                        {
                            "selected_order": 2,
                            "sample_index": 1,
                            "label": "b",
                            "device": "ATAT_2_1",
                            "source": str(root / "ATAT_photocurrent_2_1.txt"),
                            "file": str(root / "ATAT_photocurrent_2_1_pair_001.csv"),
                        },
                    ],
                }
            )

            self.assertTrue(Path(result["csv_path"]).exists())
            self.assertTrue(Path(result["png_path"]).exists())
            self.assertTrue(Path(result["svg_path"]).exists())
            manifest_path = Path(result["source_manifest_path"])
            self.assertTrue(manifest_path.exists())
            manifest_text = manifest_path.read_text(encoding="utf-8")
            self.assertIn("lineshape_average_source_manifest", {o["role"] for o in result["outputs"]})
            self.assertIn("ATAT_photocurrent_1_1_pair_001.csv", manifest_text)
            self.assertIn("ATAT_photocurrent_2_1_pair_001.csv", manifest_text)
            self.assertEqual(len(result["outputs"]), 4)

    def test_lineshape_trace_data_matches_average_without_matplotlib(self) -> None:
        samples = [
            {"label": "a", "t": [-0.001, 0.0, 0.001], "y": [0.0, 2.0, 0.0]},
            {"label": "b", "t": [-0.001, 0.0, 0.001], "y": [0.0, 4.0, 0.0]},
        ]

        payload = echem_lineshape.trace_data_payload(
            {
                "samples": samples,
                "selected": [0, 1],
                "kind": "photocurrent",
                "crop_t0": -0.001,
                "crop_t1": 0.001,
            },
            max_points=100,
        )

        self.assertEqual(payload["title"], "Average (n=2)")
        self.assertEqual(payload["x_label"], "Time (s)")
        self.assertEqual(payload["y_label"], "Photocurrent (mA)")
        self.assertEqual(payload["n_full"], len(payload["avg_data"]["time_s"]))
        self.assertEqual(payload["n_points"], len(payload["x"]))
        center_idx = int(np.argmin(np.abs(np.asarray(payload["avg_data"]["time_s"]))))
        self.assertAlmostEqual(payload["avg_data"]["y"][center_idx], 3.0)

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

    def test_emg_trace_data_payload_reuses_decimated_shape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_emg_trace_") as tmp:
            path = Path(tmp) / "channel.csv"
            rows = ["time_s,value_uV"]
            rows.extend(f"{i * 0.001},{float(i % 7)}" for i in range(30))
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")

            service = EmgPeaksService(
                has_scipy=False,
                find_peaks=None,
                peak_widths=None,
                fig_to_b64=lambda _fig: "",
                float_or=lambda value, default: float(value)
                if value not in (None, "")
                else default,
                line_color="#3E6AE1",
                mode_is_save=lambda _mode: False,
            )
            payload = service.trace_data_payload(
                {"path": str(path), "x_min": 0.005, "x_max": 0.025},
                max_points=8,
            )

        self.assertEqual(payload["x_label"], "time_s")
        self.assertEqual(payload["y_label"], "value_uV")
        self.assertEqual(payload["n_full"], 21)
        self.assertLessEqual(payload["n_points"], 8)

    def test_emg_grouped_export_can_include_linked_channels(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dataprocess_emg_linked_") as tmp:
            folder = Path(tmp) / "trial"
            folder.mkdir()
            detect_path = folder / "trial_CH0.csv"
            linked_path = folder / "trial_CH1.csv"
            rows0 = ["time_s,value_uV"]
            rows1 = ["time_s,value_uV"]
            for i in range(11):
                t = i * 0.001
                rows0.append(f"{t},{i}")
                rows1.append(f"{t},{100 + i}")
            detect_path.write_text("\n".join(rows0) + "\n", encoding="utf-8")
            linked_path.write_text("\n".join(rows1) + "\n", encoding="utf-8")

            service = EmgPeaksService(
                has_scipy=False,
                find_peaks=None,
                peak_widths=None,
                fig_to_b64=lambda _fig: "",
                float_or=lambda value, default: float(value)
                if value not in (None, "")
                else default,
                line_color="#3E6AE1",
                mode_is_save=lambda mode: mode == "save",
            )
            result = service.grouped_export_payload(
                {
                    "path": str(detect_path),
                    "peaks": [{"peak_idx": 5, "time_s": 0.005, "group": "A"}],
                    "linked_channels": [linked_path.name],
                    "half_ms": 1.0,
                    "mode": "save",
                }
            )["data"]

            linked_segments = [
                Path(path)
                for path in result["segment_paths"]
                if Path(path).parent.name == "A_CH1"
            ]
            self.assertEqual(len(linked_segments), 1)
            linked_df = pd.read_csv(linked_segments[0])

        self.assertEqual(result["segment_count"], 2)
        self.assertEqual(result["linked_channel_count"], 1)
        self.assertIn("CH0", result["segment_channels"])
        self.assertIn("CH1", result["segment_channels"])
        self.assertIn("source_channel", linked_df.columns)
        self.assertEqual(set(linked_df["source_channel"]), {"CH1"})
        self.assertTrue(((linked_df["t_rel_ms"] >= -1.001) & (linked_df["t_rel_ms"] <= 1.001)).all())


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
