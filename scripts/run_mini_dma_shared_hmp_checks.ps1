$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $repoRoot

$artifacts = Join-Path $repoRoot "artifacts"
$env:QT_QPA_PLATFORM = "offscreen"
$env:MPLBACKEND = "Agg"
$env:TEMP = Join-Path $artifacts "tool-temp"
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $artifacts "uv-cache"
$env:MPLCONFIGDIR = Join-Path $artifacts "mpl-cache"
New-Item -ItemType Directory -Force $env:TEMP, $env:UV_CACHE_DIR, $env:MPLCONFIGDIR, (Join-Path $artifacts "pytest-temp") | Out-Null

& .\.venv\Scripts\python.exe -m pytest `
    --basetemp artifacts\pytest-temp `
    -p no:cacheprovider `
    tests\test_mini_dma_logger.py::test_tic_status_falls_back_when_full_status_times_out `
    tests\test_mini_dma_logger.py::test_tic_status_raises_instead_of_returning_device_list `
    tests\test_mini_dma_logger.py::test_tic_controller_prefers_native_usb_when_requested `
    tests\test_mini_dma_logger.py::test_libusb_wheel_library_finder_accepts_bundled_dll `
    tests\test_mini_dma_logger.py::test_native_tic_usb_controller_accepts_single_device_when_serial_string_unreadable `
    tests\test_mini_dma_logger.py::test_native_tic_usb_controller_rejects_ambiguous_unreadable_serials `
    tests\test_mini_dma_logger.py::test_tic_controller_falls_back_to_ticcmd_when_native_status_call_fails `
    tests\test_mini_dma_logger.py::test_tic_controller_falls_back_to_ticcmd_when_native_move_call_fails `
    tests\test_mini_dma_logger.py::test_manual_jog_press_uses_recent_good_tic_status_without_blocking_refresh `
    tests\test_mini_dma_logger.py::test_manual_jog_press_refreshes_stale_tic_status `
    tests\test_mini_dma_logger.py::test_supply_channels_default_to_unselected_and_have_no_profile_default_label `
    tests\test_mini_dma_logger.py::test_current_sweep_channel_setup_requires_explicit_channel `
    tests\test_mini_dma_logger.py::test_motor_supply_enable_requires_explicit_channel `
    tests\test_mini_dma_logger.py::test_shared_broker_supply_controller_leases_current_and_motor_channels `
    tests\test_mini_dma_logger.py::test_shared_broker_profile_builds_broker_supply_controller `
    tests\test_mini_dma_logger.py::test_shared_broker_supply_connect_validates_broker_snapshot `
    tests\test_mini_dma_logger.py::test_motor_supply_enable_fails_when_output_readback_stays_off `
    tests\test_mini_dma_logger.py::test_shared_broker_auto_connect_starts_local_broker_when_endpoint_is_down `
    tests\test_mini_dma_logger.py::test_shared_broker_preflight_connects_without_serial_auto_detect `
    tests\test_mini_dma_logger.py::test_native_tic_usb_controller_sends_control_transfers `
    tests\test_mini_dma_logger.py::test_native_tic_usb_controller_formats_status `
    tests\test_mini_dma_logger.py::test_tic_controller_auto_falls_back_to_ticcmd_when_native_usb_unavailable `
    tests\test_mini_dma_logger.py::test_manual_auto_connect_enables_motor_supply_before_tic_status `
    tests\test_shared_power_supply_broker.py `
    tests\test_current_annealing_logger.py::test_shared_broker_run_writes_measurements_to_log
