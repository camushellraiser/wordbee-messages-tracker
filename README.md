# Wordbee Mail Explorer

A polished Streamlit app for searching Apple Mail **mbox** exports from the `wordbee` folder.

## What it does
- Upload the exported **mbox** file
- Search by the **numeric job ID only**
- Match IDs in both message styles:
  - one GTS identifier
  - two GTS identifiers, where the second is the key one
- Also handle messy real-world formatting like `GTS260030_Web...`
- Show results in chronological order
- Display only the text between the two dashed separator lines
- Download matched results as CSV
- Reset everything with one click

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
