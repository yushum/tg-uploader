import os

with open("uploader.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Fix 1: get_upload_message_id
for i, line in enumerate(lines):
    if "def get_upload_message_id" in line:
        for j in range(i, i+15):
            if "search_pattern = os.path.join(dir_path, safe_prefix) +" in lines[j]:
                lines[j] = "    raw_pattern = os.path.join(dir_path, prefix_pattern)\n"
                lines.insert(j+1, "    safe_pattern = raw_pattern.replace('\\\\', '\\\\\\\\').replace('%', '\\\\%').replace('_', '\\\\_') + \"%\"\n")
                lines[j+2] = lines[j+2].replace("(search_pattern,)", "(safe_pattern,)")
                break
        break

# Fix 2: generate_thumbnail
for i, line in enumerate(lines):
    if "thumb_path = os.path.join('/tmp'," in line and "uuid" not in line:
        lines[i] = "        thumb_path = os.path.join('/tmp', f\"{os.path.basename(video_path)}_{uuid.uuid4().hex[:8]}.thumb.jpg\")\n"
        break

# Fix 3: conversion temp path
for i, line in enumerate(lines):
    if "upload_target_path = os.path.join('/app/session'" in line:
        lines.insert(i, "            os.makedirs(os.path.dirname(SESSION_NAME), exist_ok=True)\n")
        lines[i+1] = "            upload_target_path = os.path.join(os.path.dirname(SESSION_NAME), f\"{filename_no_ext}_{unique_suffix}_converted.mp4\")\n"
        break

# Fix 4: scan_and_upload FAILED retry logic
for i, line in enumerate(lines):
    if "active_upload_tasks = {}" in line:
        lines.insert(i+1, "    fail_counts = {}\n")
        break

for i, line in enumerate(lines):
    if "if status in ('COMPLETED', 'SKIPPED_FOR_NATIVE_MP4', 'SKIPPED_DUPLICATE_MP4', 'SKIPPED_EMPTY_FILE', 'SKIPPED_FOR_SPLIT', 'FAILED'):" in line:
        lines[i] = "                if status in ('COMPLETED', 'SKIPPED_FOR_NATIVE_MP4', 'SKIPPED_DUPLICATE_MP4', 'SKIPPED_EMPTY_FILE', 'SKIPPED_FOR_SPLIT'):\n                    continue\n                if status == 'FAILED':\n                    fail_counts[path_str] = fail_counts.get(path_str, 0) + 1\n                    if fail_counts[path_str] >= 3:\n                        continue\n"
        break

# Fix 5: try-except around entire upload_file logic
# find async with semaphore:
start_idx = -1
for i, line in enumerate(lines):
    if "async with semaphore:" in line:
        start_idx = i + 1
        break

# find end of upload_file (where _sync_scan_directories starts)
end_idx = -1
for i, line in enumerate(lines):
    if "def _sync_scan_directories" in line:
        end_idx = i - 1
        break

# Currently, there's a try block starting at line 667: "except Exception as e:"
# But wait, it's already there! Let's find it.
except_idx = -1
for i in range(start_idx, end_idx):
    if "except Exception as e:" in lines[i] and "Failed to upload" in lines[i+1]:
        except_idx = i
        break

# We need to insert a `try:` after `async with semaphore:` and indent everything.
# Wait! Instead of indenting everything, what if we just factor out the core logic into another async function `_process_and_upload_file` and call it inside a try block?
# That avoids modifying indentation of 400 lines!
# But actually, Python makes it very easy to just indent.
# Wait, if we indent everything, git diff will be messy.
# Python allows: 
# try:
#     pass
# except Exception:
#     pass
# But wait, the original code already has a try block!
# Let's see where the original try block is.
# Actually, I'll just write this python script to do it.

# wait, I will just do string replacement of the `try:` block.
# Where was the `try:`?
try_idx = -1
for i in range(except_idx-1, start_idx, -1):
    if lines[i].strip() == "try:":
        try_idx = i
        break

if try_idx != -1 and except_idx != -1:
    # Remove the `try:` line and unindent lines inside?
    # No, we want to move `try:` up to `start_idx + 3`
    lines.pop(try_idx)
    except_idx -= 1
    
    # insert `try:` at start
    lines.insert(start_idx + 4, "        try:\n")
    
    # Indent lines from start_idx + 5 to except_idx
    for k in range(start_idx + 5, except_idx + 1):
        if lines[k].strip() != "":
            lines[k] = "    " + lines[k]

with open("uploader.py", "w", encoding="utf-8") as f:
    f.writelines(lines)
print("Done!")
