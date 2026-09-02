"""RollingLMDB replaces the old LMDB lock tests.

RollingLMDB eliminates the need for compaction (rolling deletion
removes old chunks instead) and each chunk is a standalone LMDB
environment, so no global lock is needed.

These tests verify basic put/get through ``database.snapshot_db``.
"""
