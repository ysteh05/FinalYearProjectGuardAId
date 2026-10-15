# YOLOv9-T Full Dataset V1 on Brev

This folder contains the same two-class training, validation, threshold selection,
test, inference and browser dashboard code used by the local workbench. The
remote dashboard binds to `127.0.0.1` on the Brev machine; access it through the
Brev SSH port-forward tunnel rather than exposing the service publicly.

## Start on the Brev instance

```bash
cd /home/shadeform/weapon_yolo_v1
./model_code/brev_remote_start.sh
```

The page runs on the Brev host at `http://127.0.0.1:8767/full-dataset-v1`.
Start the local tunnel from the Windows WSL Ubuntu distribution:

```bash
~/.local/bin/brev port-forward alright-yellow-bee -p 8767:8767
```

Then open `http://127.0.0.1:8767/full-dataset-v1` in the local browser. Keep the
port-forward command running while viewing the page. Training, validation,
threshold calibration, final test and image inference execute on Brev's A6000;
the browser only controls the remote workbench and reads its live status.

## Python environment

Install the CUDA-enabled PyTorch wheel first, then the YOLO dashboard packages:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
python -m pip install -r model_code/requirements-yolov9-dashboard.txt
```

The first dataset audit reads the uploaded raw source folders and creates a
Linux-path cache on Brev. It does not require copying the Windows cache. The
cache builder copies images into its private training view so YOLOv9's JPEG
repair path cannot write through a hardlink into the uploaded raw sources.

## Stop the dashboard

```bash
./model_code/brev_remote_stop.sh
```

Stopping the browser server does not intentionally send a training stop command;
check the page and run state before stopping or restarting the Brev instance.
