# Pre-v15 system baseline

This branch contains the complete sanitized code snapshot, tests and database design, not the mobile-only main layout. Real DB, media and local credentials are in the separately verified local private backup. Restore the original mobile .gitignore and local Firebase files from that backup. Model weights are intentionally excluded; re-use the existing model paths and calibration configuration. runtime_dependencies contains the small YOLO runtime source and threshold configuration.
