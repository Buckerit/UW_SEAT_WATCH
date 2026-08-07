from __future__ import annotations

import asyncio

from app.services.jobs import run_polling_job


async def main() -> None:
    summary = await run_polling_job()

    print()
    print("Polling summary")
    print("----------------")
    print(f"Active watches:      {summary.active_watches}")
    print(f"Distinct courses:    {summary.distinct_courses}")
    print(f"Successful courses:  {summary.successful_courses}")
    print(f"Failed courses:      {summary.failed_courses}")
    print(f"Missing sections:    {summary.missing_sections}")
    print(f"Opening events:      {len(summary.opening_events)}")

    for event in summary.opening_events:
        print()
        print(
            f"OPEN: {event.subject} "
            f"{event.catalog_number} "
            f"{event.section_name}"
        )
        print(f"Email: {event.email}")
        print(
            "Changed from "
            f"{event.previous_enrollment}/"
            f"{event.previous_capacity} to "
            f"{event.current_enrollment}/"
            f"{event.current_capacity}"
        )
        print(
            f"Potential seats: {event.available_seats}"
        )


if __name__ == "__main__":
    asyncio.run(main())
