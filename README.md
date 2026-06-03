# Wordbee Mail Explorer

A polished Streamlit app for searching Apple Mail **mbox** exports from the `wordbee` folder.

## What it does
- Upload the exported **mbox** file
- Search by the **numeric job ID only**
- Match IDs in both message styles:
  - one GTS identifier
  - two GTS identifiers, where the second is the key one
- Handle realistic Wordbee formatting like `GTS260030_Web...`
- Extract only the content between the separator lines
- Preserve the `Click to access job online` link when present
- Show results in chronological order
- Download matched results as CSV
- Reset everything with one click

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
