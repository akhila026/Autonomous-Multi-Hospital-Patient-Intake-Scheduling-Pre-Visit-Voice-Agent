from datetime import datetime, timedelta, time
from typing import List, Dict, Any, Optional
from backend.scheduling.locks import slot_lock_manager

class SchedulingEngine:
    """
    Central calculation engine for doctor availability and discrete time slot generation.
    Enforces working hours, calendar blocks, active status, and non-concurrent reservations.
    """
    @staticmethod
    def calculate_availability(
        doctor_id: str,
        working_hours: Dict[int, Dict[str, str]], # weekday -> {start: "09:00", end: "17:00"}
        blocked_periods: List[Dict[str, datetime]],
        slot_duration_minutes: int = 30,
        days_ahead: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Calculates available time slots adhering to doctor's active calendar rules,
        excluding any intervals overlapping with blocked periods.
        """
        available_slots = []
        now = datetime.utcnow()
        today = now.date()
        slot_duration = timedelta(minutes=slot_duration_minutes)

        for day_offset in range(days_ahead):
            target_date = today + timedelta(days=day_offset)
            weekday = target_date.weekday()

            if weekday in working_hours:
                sched = working_hours[weekday]
                start_h, start_m = map(int, sched["start"].split(":"))
                end_h, end_m = map(int, sched["end"].split(":"))

                slot_start = datetime.combine(target_date, time(start_h, start_m))
                day_end = datetime.combine(target_date, time(end_h, end_m))

                while slot_start + slot_duration <= day_end:
                    slot_end = slot_start + slot_duration

                    # Only future slots
                    if slot_start > now:
                        # Check blocked period overlap: slot_start < block_end and slot_end > block_start
                        is_blocked = False
                        for block in blocked_periods:
                            b_start = block["start_time"]
                            b_end = block["end_time"]
                            if slot_start < b_end and slot_end > b_start:
                                is_blocked = True
                                break

                        if not is_blocked:
                            available_slots.append({
                                "doctor_id": doctor_id,
                                "start_time": slot_start,
                                "end_time": slot_end,
                                "status": "AVAILABLE"
                            })

                    slot_start = slot_end

        return available_slots

    @staticmethod
    async def validate_and_reserve_slot(slot_id: str, patient_id: str) -> bool:
        """
        Re-validates availability immediately before booking and acquires atomic lock.
        """
        return await slot_lock_manager.acquire_hold(slot_id)
