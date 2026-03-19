# StormLeads — Mac Setup Guide

Follow these steps in order. Open **Terminal** (or the terminal inside VS Code).

---

## Step 1: Update Python

Check what you have:
```bash
python3 --version
```

If it's below 3.11, install the latest Python:
```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3.12+
brew install python@3.12
```

Verify it worked:
```bash
python3 --version
# Should show Python 3.12.x or higher
```

---

## Step 2: Create your project folder

Pick where you want the project to live. Your Desktop or a `projects` folder works fine:
```bash
# Create a projects folder (skip if you already have one)
mkdir -p ~/projects

# Move into it
cd ~/projects
```

Now take the `stormleads` folder that Claude gave you and move it here.
You can drag it into Finder at `~/projects/`, or if you downloaded it:
```bash
# If it downloaded as a zip or folder to Downloads:
mv ~/Downloads/stormleads ~/projects/stormleads
```

Then open it in VS Code:
```bash
cd ~/projects/stormleads
code .
```

---

## Step 3: Set up a virtual environment

This keeps your project's packages separate from your system Python.
Run these in the VS Code terminal (Terminal → New Terminal):

```bash
# Create the virtual environment
python3 -m venv venv

# Activate it (you'll see "(venv)" appear in your terminal prompt)
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip
```

**Important:** Every time you open a new terminal to work on this project,
you need to activate the venv again:
```bash
cd ~/projects/stormleads
source venv/bin/activate
```

VS Code can do this automatically — when it asks "select interpreter",
pick the one that says `./venv/bin/python`.

---

## Step 4: Install dependencies

With your venv activated:
```bash
pip install -r requirements.txt
```

This installs `httpx` (the HTTP client for calling NWS/SPC APIs).

---

## Step 5: Run the storm pipeline

```bash
python main.py
```

**What you'll see:**
- Log messages showing it's fetching NWS alerts and SPC reports
- If there are active storms in KC, you'll see damage zones with scores
- If it's clear weather, you'll see "No storm events found — clear skies!"
- Either way, it creates a `damage_zones_latest.json` file

**Note:** If there are no current storms, that's fine! The dashboard
(Step 6) includes sample data so you can see what it looks like.

---

## Step 6: Run the dashboard

After running the pipeline at least once:
```bash
pip install fastapi uvicorn

python dashboard.py
```

Then open your browser to: **http://localhost:8000**

You'll see a map of KC metro with damage zones and lead targets.

---

## Project structure after setup
```
stormleads/
├── venv/                     ← Python virtual environment (don't edit)
├── config/
│   └── settings.py           ← All settings (edit thresholds here)
├── src/
│   └── weather/
│       ├── models.py          ← Data models
│       ├── nws_client.py      ← NWS API client
│       ├── spc_client.py      ← SPC storm reports
│       └── storm_tracker.py   ← Core storm processor
├── dashboard.py               ← Web dashboard (map UI)
├── main.py                    ← Run the pipeline
├── requirements.txt
├── damage_zones_latest.json   ← Output (created after first run)
└── SETUP_MAC.md               ← This file
```

---

## Troubleshooting

**"command not found: python3"**
→ Run `brew install python@3.12` and try again.

**"No module named httpx"**
→ Make sure your venv is activated: `source venv/bin/activate`
→ Then: `pip install -r requirements.txt`

**"No storm events found"**
→ This is normal when there's no severe weather in KC.
→ The dashboard has sample data built in for testing.

**Network errors from NWS API**
→ The NWS API sometimes goes down briefly. Wait a few minutes and retry.
→ SPC CSV downloads are more reliable.

**VS Code says "select Python interpreter"**
→ Click it and choose `./venv/bin/python` — this ensures VS Code
  uses your virtual environment.
