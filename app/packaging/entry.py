"""Frozen desktop entry point. No machine-specific state is bundled."""
import multiprocessing
import sys

if __name__ == '__main__':
    multiprocessing.freeze_support()
    from twwiki.__main__ import main
    main()
