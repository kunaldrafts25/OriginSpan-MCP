# OriginSpan GitHub container package

Image: `ghcr.io/kunaldrafts25/originspan-mcp:0.5.2rc2`.

This Linux amd64 pilot image packages the exact checksummed application wheel from the published GitHub release. It includes PostgreSQL client support, runs as a nonroot user and defaults to MCP stdio. It does not contain model weights, llama.cpp, customer sources or a preanalyzed application. A separately validated runtime/model is required for local-model inference inside a container.

## Use the package

After the image is published and its visibility is public:

```console
docker pull ghcr.io/kunaldrafts25/originspan-mcp:0.5.2rc2
docker run --rm ghcr.io/kunaldrafts25/originspan-mcp:0.5.2rc2 --help
docker volume create originspan-state
docker run --rm -i -v originspan-state:/state ghcr.io/kunaldrafts25/originspan-mcp:0.5.2rc2
```

The final command starts an MCP stdio server for a client; it is not an interactive chatbot or public HTTP endpoint. Configure the MCP client to run `docker` with the arguments `run --rm -i -v originspan-state:/state ghcr.io/kunaldrafts25/originspan-mcp:0.5.2rc2`.

Register/analyze an approved source snapshot using the same named state volume before discovery. Bind-mount only explicitly approved sources read-only, and pass their in-container path as the trusted source root. The container cannot access arbitrary host files without an explicit mount. GitHub source import and neural embeddings are disabled by default.

Shared HTTP serving is an explicit deployment configuration using authentication, a durable catalog and managed sources; publishing this image does not qualify such a deployment.

## Publication and visibility

The manual **Publish verified MCP container** workflow verifies the application release checksum, builds the image, checks nonroot execution and CLI startup, initializes real MCP stdio with Docker networking disabled, then pushes the versioned tag. It links the package to this repository using GitHub's workflow identity and OCI source label. It refuses to replace an existing version tag.

GitHub may initially create the container package with private visibility, even for a public repository. The package owner can open its **Package settings**, choose **Change visibility**, select **Public**, and confirm the package name. Verify anonymous pulling after that change.

PyPI hosts the Python distribution; GitHub Packages hosts this container. They are separate distribution formats for the same application version. The image has not been qualified for ARM64 or GPU/local-model execution.
