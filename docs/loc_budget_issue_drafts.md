# LOC Budget Issue Drafts

Generated on 2026-05-14 because the local environment does not have `gh`
installed, so issues could not be created directly from this workspace.

Each section below is ready to copy into a GitHub issue.

## Classification

| File | Current decision |
|---|---|
| `web_api/fluorescence_gif_kymograph_routes.py` | Should split |
| `web_api/lif_viewer.py` | Should split |
| `web_api/fluorescence_gif_basic_routes.py` | Should split |
| `web_api/echem_pv.py` | Should split |
| `web_api/fluorescence_roi_sequence_routes.py` | Should split |
| `web_api/fluorescence_stack_routes.py` | Should split |
| `web_api/echem_pc.py` | Should split |
| `web_api/echem_lineshape.py` | Should split |
| `web_api/fluorescence_gif_roi_analysis_routes.py` | Should split |
| `web_api/fluorescence_request_schemas.py` | LOC budget exception: schema catalog |
| `web_api/csv_viewer.py` | Should split |
| `web_api/fluorescence_roi_export_routes.py` | Should split |
| `web_api/fluorescence_roi_basic_routes.py` | Should split |
| `web_api/abf_viewer.py` | Should split |
| `web_api/emg_peaks.py` | Should split |
| `web_api/fluorescence_3d_routes.py` | Should split |

## web_api/fluorescence_gif_kymograph_routes.py

Title: Split `web_api/fluorescence_gif_kymograph_routes.py` below the route LOC budget

Body:
`web_api/fluorescence_gif_kymograph_routes.py` exceeds the 200-line route budget.
Move kymograph preview/export workflow helpers into `services/fluorescence/` so
the route module only validates requests, calls service functions, and
serializes responses.

Acceptance criteria:
- Route module is under 200 LOC.
- Service files remain under 600 LOC.
- Existing kymograph preview/export tests or smoke coverage pass.

## web_api/lif_viewer.py

Title: Split `web_api/lif_viewer.py` below the route LOC budget

Body:
`web_api/lif_viewer.py` exceeds the 200-line route budget. Move remaining LIF
response assembly into focused `services/fluorescence/lif_*` helpers and keep
route handlers as thin validation/service/serialization wrappers.

Acceptance criteria:
- Route module is under 200 LOC.
- LIF service modules stay under 600 LOC.
- LIF preview, metadata, manifest export, TIFF export, and 3D export smoke
  coverage pass.

## web_api/fluorescence_gif_basic_routes.py

Title: Split `web_api/fluorescence_gif_basic_routes.py` below the route LOC budget

Body:
`web_api/fluorescence_gif_basic_routes.py` exceeds the 200-line route budget.
Split GIF preview, render, and merge response assembly into focused service
workflow helpers.

Acceptance criteria:
- Route module is under 200 LOC.
- Preview/render/merge endpoints keep the same response contract.
- Background job endpoints call service functions directly.

## web_api/echem_pv.py

Title: Split `web_api/echem_pv.py` below the route LOC budget

Body:
`web_api/echem_pv.py` exceeds the 200-line route budget. Move photovoltage
loading, plotting, detection response assembly, and export payload generation
into `services/echem*` helpers.

Acceptance criteria:
- Route module is under 200 LOC.
- Plot/export service behavior has focused tests.
- Existing PV endpoints keep their current response contract.

## web_api/fluorescence_roi_sequence_routes.py

Title: Split `web_api/fluorescence_roi_sequence_routes.py` below the route LOC budget

Body:
`web_api/fluorescence_roi_sequence_routes.py` exceeds the 200-line route budget.
Move ROI sequence analysis plotting, image assembly, and export helpers into the
fluorescence ROI service layer.

Acceptance criteria:
- Route module is under 200 LOC.
- Sequence analysis service functions are testable without Flask.
- Existing analyze-sequence response shape is preserved.

## web_api/fluorescence_stack_routes.py

Title: Split `web_api/fluorescence_stack_routes.py` below the route LOC budget

Body:
`web_api/fluorescence_stack_routes.py` exceeds the 200-line route budget. Split
stack browse, info, preview, defaults, export, normalization, and batch metadata
payload assembly into focused services.

Acceptance criteria:
- Route module is under 200 LOC.
- Stack service files stay under 600 LOC.
- Stack export and normalize jobs call services directly.

## web_api/echem_pc.py

Title: Split `web_api/echem_pc.py` below the route LOC budget

Body:
`web_api/echem_pc.py` exceeds the 200-line route budget. Move photocurrent
loading, plotting, detection response assembly, and export payload generation
into `services/echem*` helpers.

Acceptance criteria:
- Route module is under 200 LOC.
- Plot/export service behavior has focused tests.
- Existing PC endpoints keep their current response contract.

## web_api/echem_lineshape.py

Title: Split `web_api/echem_lineshape.py` below the route LOC budget

Body:
`web_api/echem_lineshape.py` exceeds the 200-line route budget. Move line-shape
file loading, average plotting, grid plotting, and average export payload logic
into the echem service layer.

Acceptance criteria:
- Route module is under 200 LOC.
- Plot and export behavior is covered by service tests.
- Job endpoint calls the same service function as the direct route.

## web_api/fluorescence_gif_roi_analysis_routes.py

Title: Split `web_api/fluorescence_gif_roi_analysis_routes.py` below the route LOC budget

Body:
`web_api/fluorescence_gif_roi_analysis_routes.py` exceeds the 200-line route
budget. Move GIF ROI analysis/export workflow helpers into
`services/fluorescence/`.

Acceptance criteria:
- Route module is under 200 LOC.
- ROI analysis and export services can run outside Flask.
- Existing job endpoints call service functions directly.

## web_api/csv_viewer.py

Title: Split `web_api/csv_viewer.py` below the route LOC budget

Body:
`web_api/csv_viewer.py` exceeds the 200-line route budget. Move remaining CSV
download/export response assembly and schema-heavy wrappers out of the route
module.

Acceptance criteria:
- Route module is under 200 LOC.
- CSV export/download behavior is preserved.
- Service tests cover merge/export payload generation.

## web_api/fluorescence_roi_export_routes.py

Title: Split `web_api/fluorescence_roi_export_routes.py` below the route LOC budget

Body:
`web_api/fluorescence_roi_export_routes.py` exceeds the 200-line route budget.
Move ROI sequence image and GIF export assembly into fluorescence ROI services.

Acceptance criteria:
- Route module is under 200 LOC.
- Export services are testable without Flask.
- Direct and job endpoints share the same service path.

## web_api/fluorescence_roi_basic_routes.py

Title: Split `web_api/fluorescence_roi_basic_routes.py` below the route LOC budget

Body:
`web_api/fluorescence_roi_basic_routes.py` exceeds the 200-line route budget.
Move ROI stack loading, preview rendering, and analysis response assembly into
the fluorescence ROI service layer.

Acceptance criteria:
- Route module is under 200 LOC.
- ROI load/analyze behavior is covered by service tests.
- Existing response shape is preserved.

## web_api/abf_viewer.py

Title: Split `web_api/abf_viewer.py` below the route LOC budget

Body:
`web_api/abf_viewer.py` exceeds the 200-line route budget. Move ABF request
schemas or remaining export wrappers out so the route module stays below the
route-only budget.

Acceptance criteria:
- Route module is under 200 LOC.
- ABF schemas remain visible in OpenAPI.
- Existing ABF browse/plot/detect/export tests pass.

## web_api/emg_peaks.py

Title: Split `web_api/emg_peaks.py` below the route LOC budget

Body:
`web_api/emg_peaks.py` exceeds the 200-line route budget. Move EMG request
schemas or legacy compatibility wrappers out so the route module stays below
budget while retaining both grouped and legacy peak export endpoints.

Acceptance criteria:
- Route module is under 200 LOC.
- OpenAPI schema refs remain stable.
- Existing EMG export endpoints preserve response contracts.

## web_api/fluorescence_3d_routes.py

Title: Split `web_api/fluorescence_3d_routes.py` below the route LOC budget

Body:
`web_api/fluorescence_3d_routes.py` exceeds the 200-line route budget. Move
remaining 3D TIFF info, preview, export, rotation GIF, and intensity
distribution response assembly into `services/fluorescence/volume3d_*` helpers.

Acceptance criteria:
- Route module is under 200 LOC.
- Volume/render/export service files remain under 600 LOC.
- Existing 3D preview/export endpoints keep their response contract.
