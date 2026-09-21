import threading
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional
from contextlib import contextmanager

class SlotLockManager:
    """
    Manages atomic in-memory and concurrency slot reservation locks to prevent concurrent double-booking.
    (PRD Section 7: Patient A & Patient B concurrent booking -> only one succeeds).
    Thread-safe and async-compatible.
    """
    def __init__(self):
        self._meta_lock = threading.Lock()
        self._thread_locks: Dict[str, threading.Lock] = {}
        self._async_locks: Dict[str, asyncio.Lock] = {}
        self._held_slots: Dict[str, datetime] = {}  # slot_id -> expiration_datetime

    def get_thread_lock(self, slot_id: str) -> threading.Lock:
        with self._meta_lock:
            if slot_id not in self._thread_locks:
                self._thread_locks[slot_id] = threading.Lock()
            return self._thread_locks[slot_id]

    def get_lock(self, slot_id: str) -> asyncio.Lock:
        with self._meta_lock:
            if slot_id not in self._async_locks:
                self._async_locks[slot_id] = asyncio.Lock()
            return self._async_locks[slot_id]

    @contextmanager
    def lock(self, slot_id: str):
        """Thread-safe context manager lock per slot_id."""
        t_lock = self.get_thread_lock(slot_id)
        acquired = t_lock.acquire(timeout=10.0)
        try:
            yield acquired
        finally:
            if acquired:
                t_lock.release()

    def acquire_hold_sync(self, slot_id: str, hold_duration_minutes: int = 5) -> bool:
        """Atomically acquires a temporary hold on a slot synchronously with thread safety."""
        with self.lock(slot_id):
            now = datetime.utcnow()
            if slot_id in self._held_slots and self._held_slots[slot_id] > now:
                return False
            self._held_slots[slot_id] = now + timedelta(minutes=hold_duration_minutes)
            return True

    async def acquire_hold(self, slot_id: str, hold_duration_minutes: int = 5) -> bool:
        """Atomically acquires a temporary hold on a slot asynchronously."""
        async with self.get_lock(slot_id):
            now = datetime.utcnow()
            # If already held and not expired, hold fails
            if slot_id in self._held_slots and self._held_slots[slot_id] > now:
                return False
            
            self._held_slots[slot_id] = now + timedelta(minutes=hold_duration_minutes)
            return True

    def release_hold_sync(self, slot_id: str):
        """Releases provisional hold on a slot synchronously."""
        with self.lock(slot_id):
            self._held_slots.pop(slot_id, None)

    async def release_hold(self, slot_id: str):
        """Releases provisional hold on a slot asynchronously."""
        async with self.get_lock(slot_id):
            self._held_slots.pop(slot_id, None)

slot_lock_manager = SlotLockManager()
