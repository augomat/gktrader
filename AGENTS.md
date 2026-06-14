# Agent Notes

## Restarting Local Services After Code Changes

- After every run, commit the changes made to the modified files before closing the task.
- For changes under `src/` in local dev, a full image rebuild is usually not required because `compose.dev.yaml` bind-mounts `./src` into the `api`, `worker`, `scheduler`, and `migrate` containers.
- Running services still need a restart to load updated Python code. A live bind mount alone is not enough for long-running Python processes.
- `./gkt-start.sh` runs `docker compose ... up -d` and is useful for starting the stack, but it is not the right assumption for refreshing already-running services. If containers are already up, `up -d` may leave them running without restarting the processes.
- For code-only changes, prefer `./gkt-restart.sh`. It restarts `api` and `worker` only, which keeps impact low for normal application-code reloads.

```bash
./gkt-restart.sh
```

- If the changed code affects scheduled polling or beat-side logic, include the scheduler too:

```bash
INCLUDE_SCHEDULER=1 ./gkt-restart.sh
```

- If container configuration, dependencies, or the image build changed, recreate instead:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d --build api worker scheduler
```
