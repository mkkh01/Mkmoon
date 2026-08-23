# Render build diagnosis

The authenticated Render workspace contains the `Mkmoon` web service at `https://mkmoon.onrender.com`, linked to `mkkh01/Mkmoon` on branch `main`.

The latest visible deploy failed before application startup. Render used Python 3.14.3 and ran the service's existing Build Command:

```text
pip install -r requirements.txt
```

The repository uses `pyproject.toml` and did not contain `requirements.txt`, so Render stopped with:

```text
ERROR: Could not open requirements file: [Errno 2] No such file or directory: 'requirements.txt'
```

No secret values were entered or recorded during this diagnosis. The safe fix is to add a generated, pinned `requirements.txt` to the repository or change Render Build Command to `pip install -e '.[test]'` without installing test extras in production. The start command must be checked separately before redeploying.
