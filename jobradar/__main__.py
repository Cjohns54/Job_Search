import argparse
import logging

from . import pipeline


def main():
    ap = argparse.ArgumentParser(prog="jobradar")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fetch, filter, score, and write outputs")
    r.add_argument("--only", help="comma list: ats,github,jobspy_regional,jobspy_custom_companies")
    r.add_argument("--no-llm", action="store_true", help="keyword scoring only")
    r.add_argument("-v", "--verbose", action="store_true")
    d = sub.add_parser("detect-ats", help="find Greenhouse/Lever/Ashby boards for companies.csv")
    d.add_argument("--all", action="store_true", help="check every company, not just custom/low-confidence")
    o = sub.add_parser("app", help="open the job tracker in your browser (Applied / Ineligible toggles)")
    o.add_argument("--port", type=int, default=8765)
    o.add_argument("--no-pull", action="store_true", help="don't git pull first / push statuses on exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if getattr(a, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if a.cmd == "run":
        pipeline.run(only=set(a.only.split(",")) if a.only else None, use_llm=not a.no_llm)
    elif a.cmd == "app":
        from .app import run as run_app
        run_app(port=a.port, pull=not a.no_pull)
    elif a.cmd == "detect-ats":
        from .detect_ats import detect
        detect(check_all=a.all)


if __name__ == "__main__":
    main()
