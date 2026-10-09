# README screenshots of herdr

The herdr images in the README are a real herdr session with Claude Code on
the left and Blueprint on the right, captured as text and drawn to PNG. No
browser and no screen recording permission are involved.

- `tollgate/`: the demo project, a small OAuth server.
- `isolated-herdr.sh`: runs herdr with its own config, state and shell, so no
  saved machines, workspaces or personal prompt show up.
- `terminal.py`: runs a command in a virtual terminal (pyte) and keeps a
  snapshot of the screen with colors.
- `render.py`: turns a snapshot into a framed SVG and a 2x PNG.
- `animate.py`: turns recorded frames into a GIF and an MP4, with the agent's
  work squeezed into a few seconds.

## Steps

From the repo root, with the dev dependencies installed (`uv sync`):

```bash
cp -R scripts/demo/tollgate ~/code/tollgate
git -C ~/code/tollgate init -q -b main && git -C ~/code/tollgate add -A
git -C ~/code/tollgate -c user.name=Tollgate -c user.email=dev@tollgate.example commit -qm "Initial Tollgate service"

# 1. Start the demo herdr in a 200x52 virtual terminal (keeps running).
OUT=/tmp/blueprint-capture
(cd ~/code/tollgate && uv run --project "$OLDPWD" python "$OLDPWD/scripts/demo/terminal.py" \
  "$OUT" 200 52 -- "$OLDPWD/scripts/demo/isolated-herdr.sh" herdr) &

# 2. Drive it. `dh` talks to the demo herdr only.
dh() { scripts/demo/isolated-herdr.sh herdr "$@"; }
dh plugin link "$PWD"
dh plugin link /path/to/herdr-agent-title        # pane titles, optional
dh pane run w1:p1 claude                          # accept the folder trust prompt once
scripts/demo/isolated-herdr.sh env HERDR_ENV=1 HERDR_PANE_ID=w1:p1 \
  bash /path/to/herdr-agent-title/agent-title.sh auto "Explain the login flow"
dh plugin action invoke seeun.blueprint.open
dh pane resize --pane w1:p1 --direction left --amount 0.1
cp "$OUT/screen.json" start.json                  # start screen
dh agent prompt w1:p1 "Explain the login flow in Blueprint" --wait
cp "$OUT/screen.json" hero.json                   # agent and diagram
dh pane send-keys w1:p2 o && sleep 2 && cp "$OUT/screen.json" picker.json

# 3. Draw. Blueprint's pane starts at column 96.
uv run python scripts/demo/render.py hero.json docs/screenshots/herdr-demo
uv run python scripts/demo/render.py start.json docs/screenshots/start-screen --title Blueprint --crop 96,1,104,51
uv run python scripts/demo/render.py picker.json docs/screenshots/file-picker --title Blueprint --crop 96,1,104,51

# 4. The animation: record frames while you type the prompt and the agent works.
#    Start fresh: quit claude and run it again, and reopen Blueprint (q, then
#    `dh plugin action invoke seeun.blueprint.open`) so it shows its start screen.
dh pane focus --pane w1:p2 --direction left       # type into Claude Code
touch "$OUT/record"; sleep 2
python3 -c 'import subprocess, time
for c in "Explain the login flow in Blueprint":
    subprocess.run(["scripts/demo/isolated-herdr.sh", "herdr", "pane", "send-text", "w1:p1", c]); time.sleep(0.06)'
python3 -c 'import time; print(time.time())' > enter.txt; dh pane send-keys w1:p1 enter
sleep 4; dh agent wait w1:p1 --timeout 600000; sleep 3; rm "$OUT/record"
uv run python scripts/demo/animate.py "$OUT/frames" "$(cat enter.txt)" docs/screenshots/herdr-demo

# 5. Clean up.
touch "$OUT/stop"; dh server stop; rm -rf ~/code/tollgate ~/.cache/blueprint-demo
```

Before publishing, read `screen.txt` next to each snapshot: Claude Code's
header shows the plan name and folder, nothing else, but check anyway.
