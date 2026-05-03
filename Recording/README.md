# Audio Recorder

Minimal Windows desktop tool for recording the audio currently playing through an output device. It uses WASAPI loopback devices, so the device you pick should match the speakers or headphones used by the browser.

## Install

```powershell
cd Recording
python -m pip install -r requirements.txt
```

## Run

```powershell
python recording_app.py
```

Or from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\Recording\run_recorder.ps1 -Install
```

After the first install, you can omit `-Install`.

If Windows has multiple Python installs, use the same interpreter that launches the app:

```powershell
py -3.10 -m pip install -r .\Recording\requirements.txt
```

## Notes

- Select the output device that matches where your browser audio is playing.
- Pick a save folder and file prefix, then press Record.
- Recordings are saved as timestamped `.wav` files.
- If a meter stays silent, check that Windows is actually sending the webpage audio to that output device.
