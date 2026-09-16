"""Compact oversized diff blocks in cortex.db timeline_json.

Why this exists
---------------
Diffs used to store every unchanged line as context, and re-edits of the same
file appended without a cap. One styles.css diff block reached 5,627,791
entries (244MB), pushing a single conversation's timeline_json to 258MB. Every
restore then paid ~8.7s of json.loads plus ~1.3s of SQLite read before a single
widget was built, which is what made the app feel permanently slow.

chat_panel.py now caps this at write time. This script repairs rows written
before that fix.

Run with Cortex CLOSED:
    python tools/compact_timeline_db.py            # report only
    python tools/compact_timeline_db.py --apply    # rewrite + VACUUM
"""
import argparse
import gc
import json
import os
import sqlite3
import sys

MAX_HUNKS = 1500          # keep in step with _DIFF_MAX_PERSIST in chat_panel.py
DB = os.path.expanduser("~/.cortex/cortex.db")


def trim_message(msg):
    """Trim diff blocks in one message. Returns entries removed."""
    removed = 0
    for b in msg.get("blocks") or []:
        if not isinstance(b, dict) or b.get("type") != "diff":
            continue
        hl = b.get("hunk_lines") or []
        if len(hl) > MAX_HUNKS:
            extra = len(hl) - MAX_HUNKS
            b["hunk_lines"] = list(hl[:MAX_HUNKS]) + [
                ["ctx", None, None,
                 "... diff trimmed for storage (%d more lines)" % extra]
            ]
            removed += extra
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit("no database at %s" % args.db)

    before = os.path.getsize(args.db)
    con = sqlite3.connect(args.db)
    cur = con.cursor()
    cur.execute("SELECT conversation_id, length(timeline_json) FROM conversations "
                "ORDER BY length(timeline_json) DESC")
    rows = cur.fetchall()

    total_removed = 0
    total_saved = 0
    for cid, ln in rows:
        if not ln or ln < 1_000_000:             # empty or already healthy
            continue
        cur.execute("SELECT timeline_json FROM conversations WHERE conversation_id=?",
                    (cid,))
        raw = cur.fetchone()[0]
        if not raw:
            continue
        try:
            tl = json.loads(raw)
        except Exception as e:
            print("  skip %s (unparseable: %s)" % (cid[:12], e))
            continue
        msgs = tl.get("messages") if isinstance(tl, dict) else tl
        if not isinstance(msgs, list):
            continue
        removed = sum(trim_message(m) for m in msgs if isinstance(m, dict))
        if not removed:
            del raw, tl
            gc.collect()
            continue
        new_raw = json.dumps(tl, ensure_ascii=False)
        saved = len(raw) - len(new_raw)
        total_removed += removed
        total_saved += saved
        print("  %s : %6.1f MB -> %6.1f MB  (%s hunk entries dropped)"
              % (cid[:12], ln / 1048576, len(new_raw) / 1048576, format(removed, ",")))
        if args.apply:
            cur.execute("UPDATE conversations SET timeline_json=? WHERE conversation_id=?",
                        (new_raw, cid))
            con.commit()
        del raw, tl, new_raw
        gc.collect()

    print("\ntotal hunk entries dropped : %s" % format(total_removed, ","))
    print("timeline_json shrink       : %.0f MB" % (total_saved / 1048576))

    if args.apply:
        print("\nVACUUM (reclaiming file space)...")
        con.isolation_level = None
        con.execute("VACUUM")
        con.close()
        after = os.path.getsize(args.db)
        print("database: %.0f MB -> %.0f MB" % (before / 1048576, after / 1048576))
    else:
        con.close()
        print("\n(report only, nothing written; re-run with --apply)")


if __name__ == "__main__":
    main()
