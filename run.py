import os
import sys
import uvicorn

def main():
    # Ensure current directory is in sys.path
    root_dir = os.path.dirname(os.path.abspath(__file__))
    if root_dir not in sys.path:
        sys.path.insert(0, root_dir)

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    display_host = "localhost" if host in ("0.0.0.0", "::") else host

    print("=" * 70)
    print("  AEGISCARE AI - HEALTHCARE PLATFORM PROTOTYPE")
    print("  Autonomous Triage & Resilient EHR Synchronization")
    print("=" * 70)
    print(f"  Server running at:  http://{display_host}:{port} (listening on {host}:{port})")
    print(f"  API Docs at:        http://{display_host}:{port}/docs")
    print("=" * 70)

    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )

if __name__ == "__main__":
    main()
