"""
MailPilot - Gmail Historical LIVE Organizer

Scans existing Gmail messages in paginated batches and organizes them
using the MailPilot classification and organization pipeline.

IMPORTANT:
This script runs in LIVE mode and can create Gmail labels/folders and
move messages. It never deletes messages.
"""

from __future__ import annotations

import sys
import time

from app.auth.gmail import GmailAuthenticator
from app.classifier.ai import AIClassificationOrchestrator
from app.classifier.providers.ollama import OllamaAIProvider
from app.classifier.resolver import OrganizationResolver
from app.mail.gmail.provider import GmailProvider
from app.organizer.engine import OrganizerEngine
from app.organizer.processor import OrganizerProcessor
from database.manager import DatabaseManager


BATCH_SIZE = 50


def main() -> int:
    print("=" * 64)
    print("MAILPILOT - GMAIL HISTORICAL LIVE ORGANIZATION")
    print("=" * 64)
    print()
    print("MODE: LIVE")
    print(f"BATCH SIZE: {BATCH_SIZE}")
    print(f"MAXIMUM BATCHES: {10}")
    print(f"MAXIMUM FETCHED MESSAGES: {BATCH_SIZE * 10}")
    print("DELETION: DISABLED")
    print()
    print("Connecting to Gmail...")
    print()

    start_time = time.time()

    try:
        # --------------------------------------------------------------
        # Database
        # --------------------------------------------------------------

        database = DatabaseManager()
        database.initialize()

        # --------------------------------------------------------------
        # Gmail
        # --------------------------------------------------------------

        authenticator = GmailAuthenticator()
        provider = GmailProvider(
            authenticator=authenticator,
        )

        # --------------------------------------------------------------
        # Classification
        # --------------------------------------------------------------

        resolver = OrganizationResolver(
            database,
        )

        ai_provider = OllamaAIProvider()

        classifier = AIClassificationOrchestrator(
            resolver=resolver,
            ai_provider=ai_provider,
            learning_manager=database,
        )

        # --------------------------------------------------------------
        # Organizer
        # --------------------------------------------------------------

        engine = OrganizerEngine(
            provider=provider,
            classifier=classifier,
        )

        processor = OrganizerProcessor(
            provider=provider,
            engine=engine,
            database=database,
        )

        print("Connected.")
        print()
        print("Starting historical mailbox scan...")
        print("-" * 64)

        # --------------------------------------------------------------
        # Progress callback
        # --------------------------------------------------------------

        totals = {
            "batches": 0,
            "fetched": 0,
            "processed": 0,
            "moved": 0,
            "untouched": 0,
            "failures": 0,
        }

        def progress_callback(
            batch_number: int,
            fetched_count: int,
            results,
        ) -> None:
            moved = sum(
                1
                for result in results
                if result.action == "moved"
            )

            untouched = sum(
                1
                for result in results
                if result.action == "left_untouched"
            )

            failures = sum(
                1
                for result in results
                if result.action
                in {
                    "move_failed",
                    "folder_resolution_failed",
                }
            )

            totals["batches"] += 1
            totals["fetched"] += fetched_count
            totals["processed"] += len(results)
            totals["moved"] += moved
            totals["untouched"] += untouched
            totals["failures"] += failures

            elapsed = time.time() - start_time

            if totals["processed"] > 0:
                rate = (
                    totals["processed"]
                    / elapsed
                    * 60
                )
            else:
                rate = 0.0

            print(
                f"Batch {batch_number:04d} | "
                f"Fetched: {fetched_count:3d} | "
                f"Processed: {len(results):3d} | "
                f"Moved: {moved:3d} | "
                f"Untouched: {untouched:3d} | "
                f"Failed: {failures:2d} | "
                f"Rate: {rate:6.1f}/min"
            )

        # --------------------------------------------------------------
        # FULL LIVE HISTORICAL SCAN
        # --------------------------------------------------------------

        processor.process_existing_paginated(
            dry_run=False,
            batch_size=BATCH_SIZE,
            max_batches=10,
            progress_callback=progress_callback,
        )

        elapsed = time.time() - start_time

        print()
        print("-" * 64)
        print("HISTORICAL SCAN COMPLETE")
        print("-" * 64)
        print()
        print(f"TOTAL BATCHES:    {totals['batches']}")
        print(f"TOTAL FETCHED:    {totals['fetched']}")
        print(f"TOTAL PROCESSED:  {totals['processed']}")
        print(f"TOTAL MOVED:      {totals['moved']}")
        print(f"TOTAL UNTOUCHED:  {totals['untouched']}")
        print(f"TOTAL FAILURES:   {totals['failures']}")
        print(
            f"ELAPSED TIME:     {elapsed / 60:.1f} minutes"
        )
        print()
        print("Gmail historical organization finished.")
        print("No messages were deleted.")

        return 0

    except KeyboardInterrupt:
        print()
        print()
        print("SCAN INTERRUPTED BY USER.")
        print("Messages already moved remain moved.")
        print("The next run can continue using processing history.")
        return 130

    except Exception as exc:
        print()
        print()
        print("=" * 64)
        print("HISTORICAL SCAN FAILED")
        print("=" * 64)
        print()
        print(f"ERROR: {type(exc).__name__}: {exc}")
        print()
        print(
            "Messages successfully moved before the error remain moved."
        )
        print(
            "Processing history is preserved."
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())