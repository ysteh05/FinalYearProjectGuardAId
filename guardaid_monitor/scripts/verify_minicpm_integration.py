"""Exercise the serving MiniCPM adapter and optionally real detectors, on private test data."""
import argparse
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

APP_ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP_ROOT))
from app.config import RuntimeConfig
from app.domain import Camera, SystemSettings
from app.inference.minicpm import MiniCpmOllamaRunner
from app.inference.orchestrator import InferenceOrchestrator
from app.media import LocalMediaStore
from app.sqlite_repository import SQLiteRepository


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--detectors",action="store_true")
    args=parser.parse_args()
    output=APP_ROOT / "test_artifacts" / ("minicpm_integration_"+datetime.now().strftime("%Y%m%d_%H%M%S"))
    output.mkdir(parents=True,exist_ok=False)
    report={"output":str(output),"calls":[],"status":"running"}
    def save():
        (output/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    runner=MiniCpmOllamaRunner()
    settings=SystemSettings()
    clips=[]
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg: raise RuntimeError("ffmpeg is required for this integration fixture")
    try:
        runner.load()
        print("[ready] MiniCPM loaded through local Ollama",flush=True)
        for name in ("VID-20220816-WA0000.mp4","VID20261014170218.mp4"):
            source=APP_ROOT.parent / "Video Test" / "GuardAId" / name
            clip=output/name
            subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-ss","15","-i",str(source),
                            "-t","4","-an","-c:v","libx264","-preset","ultrafast",str(clip)],check=True,timeout=60)
            clips.append(clip)
            for task in ("review_borderline","describe_scene"):
                started=time.perf_counter()
                value=getattr(runner,task)(clip,settings.vlm)
                row={"video":name,"task":task,"seconds":time.perf_counter()-started,
                     "result":value,"input":dict(runner.last_call_metadata)}
                assert row["input"]["fps"]==2 and row["input"]["frame_count"]==8
                assert row["input"]["thinking_nonempty"] is False
                report["calls"].append(row)
                save()
                print(json.dumps(row),flush=True)
        if args.detectors:
            config=replace(RuntimeConfig.from_env(),data_dir=output/"isolated_server",
                database_path=output/"isolated_server"/"guardaid.db",persistence_mode="sqlite",
                auth_mode="disabled",notification_mode="console",maintenance_mode=False)
            repository=SQLiteRepository(config)
            # Controlled thresholds exercise the real alert/review/final-scene
            # transition even when this nonviolent clip scores below deployment's floor.
            fixture_settings=repository.get_settings()
            fixture_settings=fixture_settings.model_copy(update={
                "violence":fixture_settings.violence.model_copy(update={
                    "sensitive_threshold":0.0,"moderate_threshold":.5,"certain_threshold":1.0}),
                "config_version":fixture_settings.config_version+1,
                "updated_by":"isolated-integration-fixture",
            })
            repository.save_settings(fixture_settings)
            report["fixture_thresholds"]=fixture_settings.violence.model_dump(mode="json")
            media=LocalMediaStore(config)
            with clips[0].open("rb") as stream:
                stored=media.save_camera_video("integration",clips[0].name,"video/mp4",stream)
            camera=repository.save_camera(Camera(name="MiniCPM integration fixture",storage_path=stored.storage_path,
                original_filename=clips[0].name,duration_seconds=4))
            pipeline=InferenceOrchestrator(config,repository,media,None)
            try:
                job=pipeline.start(camera.id)
                print("[detectors] real MC3/YOLO loaded; analysis submitted",flush=True)
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    current=pipeline.get_job(job.id)
                    if str(current.status)=="failed": raise RuntimeError(current.error)
                    if str(current.status)=="completed" and current.pending_vlm_jobs==0: break
                    time.sleep(.5)
                else: raise TimeoutError("Real pipeline did not finish")
                report["pipeline"]={"job":current.model_dump(mode="json"),
                    "alerts":[alert.model_dump(mode="json") for alert in repository.list_alerts()]}
                assert current.violence_windows_processed==1 and current.weapon_frames_processed>0
                assert current.borderline_reviews_processed==1
                assert len(repository.list_alerts())>=1
                assert all(alert.description_status=="ready" for alert in repository.list_alerts())
                print("[detectors complete] "+json.dumps({"windows":current.violence_windows_processed,
                    "weapon_frames":current.weapon_frames_processed,"alerts":len(repository.list_alerts())}),flush=True)
            finally:
                pipeline.shutdown()
        report["status"]="passed"
    except Exception as error:
        report["status"]="failed"
        report["error"]=repr(error)
        raise
    finally:
        save()
        print(f"[report] {output/'report.json'}",flush=True)


if __name__=="__main__": main()
