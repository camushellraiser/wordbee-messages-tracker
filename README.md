# Wordbee Message Tracker

A polished Streamlit app for searching Apple Mail **mbox** exports from the `wordbee` folder.

## What it does
- Upload the exported **mbox** file
- Search by the **numeric job ID only**
- Match IDs in both message styles
- Extract only the content between the dashed separator lines
- Preserve links like `Click to access details online`
- Add a red **Completed** badge only when the message contains one of the names you paste into the app and a completion phrase
- Color the **latest** result's date red when it is older than 2 days
- Add a red **Action Required** badge on the same latest overdue result
- Load and save a separate **status JSON** file with your checklist steps
- Download matched results and updated status as files
- Load an Excel tracker and surface Emmanuel IDs from the newest sheet
