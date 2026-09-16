(function () {
  "use strict";

  let cy = null;
  let graphData = null;
  const tooltip = document.getElementById("tooltip");
  const status = document.getElementById("graph-status");

  function scoreColor(score, maximum) {
    const ratio = maximum > 0 ? Math.log1p(score) / Math.log1p(maximum) : 0;
    const clamped = Math.max(0, Math.min(1, ratio));
    const red = Math.round(48 + 207 * clamped);
    const green = Math.round(176 - 128 * clamped);
    const blue = Math.round(72 - 24 * clamped);
    return `rgb(${red}, ${green}, ${blue})`;
  }

  function elementsFor(data) {
    const maxNode = Math.max(0, ...data.nodes.map((node) => node.score));
    const maxEdge = Math.max(0, ...data.edges.map((edge) => edge.score));
    const nodes = data.nodes.map((node) => ({
      data: {
        id: String(node.card_id),
        score: node.score,
        color: scoreColor(node.score, maxNode),
      },
    }));
    const edges = data.edges.map((edge) => ({
      data: {
        id: `${edge.source}-${edge.target}`,
        source: String(edge.source),
        target: String(edge.target),
        score: edge.score,
        color: scoreColor(edge.score, maxEdge),
      },
    }));
    return nodes.concat(edges);
  }

  function boxesOverlap(first, second) {
    return !(
      first.x2 + 2 < second.x1 ||
      second.x2 + 2 < first.x1 ||
      first.y2 + 2 < second.y1 ||
      second.y2 + 2 < first.y1
    );
  }

  function overlaps() {
    const nodes = cy.nodes().toArray();
    for (let i = 0; i < nodes.length; i += 1) {
      const first = nodes[i].boundingBox({ includeLabels: true, includeOverlays: false });
      for (let j = i + 1; j < nodes.length; j += 1) {
        const second = nodes[j].boundingBox({ includeLabels: true, includeOverlays: false });
        if (boxesOverlap(first, second)) return true;
      }
    }
    return false;
  }

  function expandUntilSeparated() {
    if (!overlaps()) return true;
    for (let attempt = 0; attempt < 24; attempt += 1) {
      const extent = cy.nodes().boundingBox({ includeLabels: true });
      const centerX = (extent.x1 + extent.x2) / 2;
      const centerY = (extent.y1 + extent.y2) / 2;
      cy.nodes().positions((node) => {
        const position = node.position();
        const ordinal = Number(node.id().slice(-4)) || node.index();
        const jitter = attempt === 0 ? (ordinal % 7) * 0.01 : 0;
        return {
          x: centerX + (position.x - centerX) * 1.18 + jitter,
          y: centerY + (position.y - centerY) * 1.18 - jitter,
        };
      });
      if (!overlaps()) return true;
    }
    return false;
  }

  function clearSelection() {
    cy.elements().removeClass("selected-node selected-edge");
  }

  function selectNode(node) {
    clearSelection();
    node.addClass("selected-node");
    node.connectedEdges().addClass("selected-edge");
    pycmd(`select-node:${node.id()}`);
  }

  function showTooltip(event, text) {
    tooltip.textContent = text;
    tooltip.style.left = `${event.renderedPosition.x + 12}px`;
    tooltip.style.top = `${event.renderedPosition.y + 12}px`;
    tooltip.hidden = false;
  }

  function render(data) {
    graphData = data;
    if (cy) cy.destroy();
    if (!data.nodes.length) {
      status.textContent = "No interference records yet.";
      status.hidden = false;
      return;
    }
    status.hidden = true;
    cytoscape.use(cytoscapeFcose);
    cy = cytoscape({
      container: document.getElementById("graph"),
      elements: elementsFor(data),
      autoungrabify: true,
      userPanningEnabled: true,
      userZoomingEnabled: true,
      boxSelectionEnabled: false,
      style: [
        {
          selector: "node",
          style: {
            label: "",
            width: 38,
            height: 38,
            padding: 0,
            shape: "ellipse",
            "background-color": "data(color)",
            "border-width": 2,
            "border-color": "#ffffff",
          },
        },
        {
          selector: "edge",
          style: {
            width: "mapData(score, 0, 5, 2, 7)",
            "line-color": "data(color)",
            "curve-style": "bezier",
            opacity: 0.88,
          },
        },
        {
          selector: "node.selected-node",
          style: {
            "border-width": 6,
            "border-color": "#2878d0",
            "border-opacity": 1,
          },
        },
        {
          selector: "edge.selected-edge",
          style: {
            "underlay-color": "#2878d0",
            "underlay-opacity": 1,
            "underlay-padding": 5,
            "z-index": 10,
          },
        },
      ],
    });

    cy.on("tap", "node", (event) => selectNode(event.target));
    cy.on("tap", (event) => {
      if (event.target === cy) {
        clearSelection();
        pycmd("clear-selection");
      }
    });
    cy.on("mouseover", "node", (event) => {
      showTooltip(event, `Card ${event.target.id()}\nNode score: ${event.target.data("score").toFixed(4)}`);
    });
    cy.on("mouseover", "edge", (event) => {
      showTooltip(event, `Edge score: ${event.target.data("score").toFixed(4)}`);
    });
    cy.on("mouseout", "node, edge", () => {
      tooltip.hidden = true;
    });

    const layout = cy.layout({
      name: "fcose",
      quality: "proof",
      randomize: true,
      animate: false,
      fit: false,
      nodeDimensionsIncludeLabels: true,
      nodeRepulsion: 12000,
      idealEdgeLength: 110,
      edgeElasticity: 0.45,
      nestingFactor: 0.1,
      gravity: 0.25,
      numIter: 3500,
      tile: true,
      tilingPaddingVertical: 24,
      tilingPaddingHorizontal: 24,
    });
    layout.one("layoutstop", () => {
      const separated = expandUntilSeparated();
      cy.nodes().lock();
      cy.fit(cy.elements(), 36);
      if (!separated) pycmd("layout-overlap-warning");
    });
    layout.run();
  }

  window.renderInterferenceGraph = render;
})();
