"""Local cache for metadata, media, transcripts, frames and AI analysis.

Everything is keyed by video id plus a content hash so a re-run with the
same inputs reuses work and an interrupted run can resume.
"""
