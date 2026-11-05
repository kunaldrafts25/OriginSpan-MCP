# Project activity metrics

OriginSpan uses download counts, GitHub traffic and voluntary feedback to understand adoption.
Each measures something different; none alone proves how many people actively use the project.

| Signal | Where to see it | What it means |
| --- | --- | --- |
| PyPI downloads | [PyPI Stats](https://pypistats.org/packages/originspan-mcp) and the README monthly badge | Package download events reported by PyPI Stats; repeat installs and automation can contribute. |
| Release asset downloads | [GitHub Releases](https://github.com/kunaldrafts25/OriginSpan-MCP/releases) and the README badge | Downloads of attached release files across releases, including prereleases; excludes source clones and PyPI installs. |
| Repository visits and clones | [Insights → Traffic](https://github.com/kunaldrafts25/OriginSpan-MCP/graphs/traffic), with repository push access | GitHub's recent page views, unique visitors, full clones, referring sites and popular content. Visitors are not named. |
| Firsthand usage feedback | [Share your experience](https://github.com/kunaldrafts25/OriginSpan-MCP/issues/new?template=usage-feedback.yml&title=%5BFeedback%5D%20) | Voluntary, public descriptions of installation attempts and actual use. These are self-reports, not independently verified adoption figures. |

## Check visits and clones

Sign in to GitHub with repository push access, open the repository, choose **Insights**, then **Traffic**.
GitHub shows visitor and full-clone information for the past 14 days. Traffic dates use UTC.
Views and clone information update hourly; referring sites and popular content update daily.
Check and save dated observations if you need a longer history. A later README change cannot recover older traffic that GitHub no longer provides.

See [GitHub's traffic documentation](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-traffic-to-a-repository).

## Interpret downloads and feedback

- Download totals do not identify downloaders, distinguish unique people, or confirm that installation succeeded.
- A download is not evidence of a production deployment or a downstream dependency.
- A new package can have delayed or incomplete statistics. Badges may lag because of caching or upstream availability.
- Feedback is optional. A lack of public reports does not prove a lack of users.
- These documentation changes add no application usage reporting or installation callbacks. The badges use Shields.io to display aggregate statistics.

When describing project reach, name the metric, source, time window and observation date.
Keep downloads, unique visitors, clones and confirmed usage reports separate instead of adding them together as a user count.
