# Wordbee Mail Explorer

A polished Streamlit app for searching Apple Mail **mbox** exports from the `wordbee` folder.

## What it does
- Upload the exported **mbox** file
- Search by the **numeric job ID only** (for example `260030`)
- Match the **second GTS ID** in each message
- Show results in chronological order
- Display only the text between the two dashed separator lines
- Download matched results as CSV
- Reset everything with one click

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud
Set the app entry point to:

```text
app.py
```

Then deploy from this repository or ZIP contents.
