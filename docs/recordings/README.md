# Recordings

Screen recordings of each scenario, for the case where the live demo cannot
run: a model that will not load, a machine that is not the one this was built
on, a room with no network.

They are not in git. A forty-five minute screen capture is a hundred megabytes
that changes every time the UI does, and a repository is the wrong place for
it.

Name them by scenario so the run-book's fallback is one file away:

    S1-clean-application.mp4
    S2-income-discrepancy.mp4
    ...
    S10-member-and-audit.mp4

Record after `make demo` is green, on the same build being demonstrated. A
recording of a build that no longer exists is worse than none: it shows
something nobody can reproduce in the room.
