\# NHL Analytics Platform



A modular, multi-tier hockey data platform engineered to showcase clean software architecture, local data warehouse modeling, and production-grade algorithmic data pipelines. 



\## 🏗️ Platform Architecture

1\. \*\*`1\_data\_warehouse/`\*\*: A normalized local SQLite database and automated ingestion pipeline parsing historical player regular season logs directly from the official web NHL API.

2\. \*\*`2\_broadcast\_engine/`\*\*: A Contextual Exception Engine that maps and ranks live player streaks based on their historical empirical frequency (rarity) for broadcast narrative value.

3\. \*\*`3\_prediction\_models/`\*\*: An isolated machine learning environment leveraging the structured warehouse features to run baseline evaluations and project year-end player points.

