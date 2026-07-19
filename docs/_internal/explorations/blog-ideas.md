1. what all data stores should allow: live, query, persistence, parallel scaling, files
2. the limits of data standardization: allows for consistent tooling but doesn't mean much if you don't capture enough data. and if you did that you could just transform
3. the different types of test data
4. your test data deserves an event log
5. benchmarking tool performance, what do you communicate?
6. client subscription styles and selecting
7. channel definitions, lifecycle and optimizations
8. sessions in event log associating things together
9. catching process death
10. keeping events clean and symmetric and how do you know what's alive?
11. Python contextvars are awesome for test runs
12. Opportunistic singletons meet local testing needs (and they clean up after themselves)
13. Symmetric events and maninfested views solve idempotency
14. Run manifestation from events bridges the live vs finalized run logging requirements
15. API design: signalling intent (what can we know about intent through usage like "I own this resource"), fostering discoverability (consistency) and establishing helpful limits (where you put what options)
16. The marriage of automation and interactionS
17. difference between a test and a measurement
18. Outer Loops or Inner Loops? why not both?
19. representing inner and outer loops consistently in data
