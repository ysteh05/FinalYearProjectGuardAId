# Training and dashboard regression gates

For any change to a training pipeline, dataset/cache handoff, checkpoint resume flow, or its dashboard:

- Do not treat unit tests, syntax checks, or UI/API smoke tests as proof that the real training path works. Add a regression test for the exact previously failing transition and run an end-to-end smoke path through it.
- Exercise the boundary from completed/reused cache to model initialization, first epoch status serialization, first batch, validation, checkpoint writing, and resume where applicable. Use small fixtures or a dry-run where full training is too expensive; do not skip the boundary that previously failed.
- Validate all values at persistence boundaries. In particular, convert undefined/non-finite metrics (NaN, +inf, -inf) into an explicit JSON-safe representation such as `null`, and test the initial pre-validation state as well as normal finite metrics.
- When repairing a failure, preserve the exact traceback as a regression test. Then run the focused test, the relevant full suite, and the end-to-end smoke path. Report exactly what each test exercised; never imply a broader guarantee than the evidence supports.
- Do not launch or resume an expensive training run until these gates pass. After launch, verify from fresh logs/status that the first batch and checkpoint were actually reached before reporting training as started.
- If a gate fails, stop, report the precise failing stage and whether any samples/checkpoints were produced, and fix the cause before another run. Never silently retry an expensive run.
