# Publish OriginSpan to PyPI

Repository: `kunaldrafts25/OriginSpan-MCP`. Python distribution: `originspan-mcp`.

The workflow publishes the already released and checksummed wheel, preserving the exact GitHub release artifact rather than rebuilding a different wheel under the same version. No PyPI password or long-lived token is stored in this repository.

## One time account setup

1. Create your own account at https://pypi.org/account/register/ and verify your email.
2. Configure PyPI account two-factor authentication as required by PyPI.
3. Open https://pypi.org/manage/account/publishing/ and add a **pending publisher** for a new project.
4. Select GitHub and enter these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `originspan-mcp` |
| GitHub owner | `kunaldrafts25` |
| Repository | `OriginSpan-MCP` |
| Workflow filename | `publish-pypi.yml` |
| Environment | `pypi` |

The name was available during setup but is not reserved until PyPI accepts the publisher/project. Recheck if someone else registers it. If the project already exists under your ownership, use its project publisher settings instead.

## Publish

1. Ensure the GitHub release exists and includes a wheel plus `SHA256SUMS.txt`.
2. In GitHub Actions, select **Publish verified wheel to PyPI**.
3. Select **Run workflow**, use branch `main`, and enter release tag `v0.5.2rc2`.
4. The preparation job checks that the tag is a published release, downloads its wheel, verifies the published checksum and wheel version, and runs strict metadata validation.
5. The publishing job runs in GitHub environment `pypi`, obtains a short-lived publishing identity and uploads the checked artifact to PyPI with attestations.
6. Check https://pypi.org/project/originspan-mcp/ and test in a new virtual environment:

```console
python -m pip install originspan-mcp==0.5.2rc2
originspan --help
```

A prerelease should be installed by exact version or with `--pre`. Uploaded versions cannot be silently replaced. Create a new version for changed distributions. Package publication does not include model assets or establish model quality.

If publishing reports an invalid publisher, check the exact repository, workflow filename, environment and PyPI project name. Do not paste account passwords or API tokens into issues.

Primary references: https://docs.pypi.org/trusted-publishers/ and https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/.
