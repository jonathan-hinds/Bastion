from bastion.app import main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import sys
        import traceback
        from pathlib import Path

        if getattr(sys, "frozen", False):
            log_path = Path(sys.executable).with_name("bastion_crash.log")
        else:
            log_path = Path(__file__).with_name("bastion_crash.log")
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        message = f"Bastion crashed. Details were written to:\n{log_path}"
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Bastion of the Core", 0x10)
        except Exception:
            print(message)
            try:
                input("Press Enter to close...")
            except EOFError:
                pass
        raise
