# Visual design contract

## Authoritative reference
![Windows mockup](../assets/reference/reasoning-atlas-windows-mockup.png)

The owner supplied this PNG in the current conversation after the earlier ChatGPT attachment could not be retrieved. Its local source filename is preserved as provenance in the manifest; its repository filename is English. The bytes are unchanged.
- Size: 1672 × 941 pixels; 2,026,651 bytes.
- SHA-256: f9035bc07a05908e52b4884a0521f4450efe7ed5b9c3f345f9d57796f4fb86f7.
- Visual inspection completed. See [manifest](../assets/reference/reference-manifest.json), [measurements](../assets/reference/measurements.json) and [tokens](../design/tokens.json).
The earlier generated /mnt/data filename was a source lead, not independently verified byte identity. The owner's newly supplied image is the authoritative reference.

## Preserve the composition
Dark navy/near-black desktop knowledge workspace; violet illuminated graph nodes; orange selected central node and radial connections; subtle grids, particles and orbit rings. Thin borders and modest rounded corners; compact typography; restrained purple navigation selection.
Do not replace it with a generic dashboard, bright card layout or sparse flat diagram. Preserve the hierarchy and overall density while keeping actual labels legible.

Image-coordinate rectangles, measured approximately by visual inspection:
| Region | x, y, width, height (px) | Purpose |
|---|---|---|
| Application surface | 140, 37, 1394, 855 | Comparison crop |
| Title/context bar | 140, 37, 1394, 61 | App title, subtitle, search and controls |
| Left navigation | 140, 98, 172, 794 | Primary and library navigation; quote card |
| Knowledge map | 326, 109, 934, 602 | Primary visualization |
| Research roadmap | 326, 722, 934, 162 | Five research stages |
| Focus | 1273, 109, 253, 254 | Current focus, next step, long-term goal |
| Selected node | 1273, 373, 253, 341 | Description, tags and key connections |
| Quick note | 1273, 724, 253, 162 | Persistent note input |

The surrounding laptop bezel, blue Windows wallpaper, taskbar, weather and screenshot date are presentation context. Do not draw them as fake application chrome. Window controls should follow the chosen browser/native container rather than duplicate the operating system.

## Graph geometry and starter content
Central LLM Reasoning node approximately (818, 418), radius 90 px. Main neighbors:
Mathematical Reasoning (610,285); Symbolic Logic (818,224); Physics-based Models (1019,289); Evolutionary Search (1040,435); Chemical Computing (1001,582); Meta-Reasoner (818,620); Embodied Intelligence (621,582); Neuro-symbolic Systems (587,435).
Use stable seeded positions for the reference fixture. Real data can use a layout engine, but avoid continuously moving nodes while the user reads.

Preserve satellite concepts, including proof generation, abstract algebra, formal methods, computational mathematics; knowledge graphs, formal verification, theorem proving, structured reasoning, logical representations; differential equations, causal modeling, simulation/world models, physical constraints; genetic algorithms, multi-objective optimization, automated program search, open-ended evolution; molecular computation, reaction networks, DNA computing, bio-inspired algorithms; strategy selection, self-improvement, task decomposition and reasoning about reasoning; robotics, environment interaction, sensorimotor learning, spatial reasoning; neural-symbolic integration, program induction, structured memory and interpretable AI.
These labels are research content, not implemented capabilities.

Research roadmap: LLMs → Hybrid Reasoning → Mechanism Transfer → Adaptive Architectures → Reasoning Discovery. Retain numbered stages, small node illustrations and orange connecting arrows. Do not substitute software-build phases.

## Tokens and measurement limits
Pixel samples are recorded separately from implementation tokens. Exact samples include header #020C25 at (950,70), map border area #050D26 at (1240,110), selected navigation #130740 at (225,165), note background #0A1124 at (1395,755), roadmap background #071023 at (1227,858).
The image contains gradients, glows and texture; one sample is not a universal CSS color. design/tokens.json labels derived values as approximations.
Suggested implementation palette: canvas #050A1B; panels #0B1225; violet #A76BFF; bright violet #D9B7FF; orange #FF8A32; text #F2EDFF; secondary #C1B8D8. Tune against the original.
Raster text does not reveal exact font identity. Start with Segoe UI/system-ui; match weight, width and line breaks visually. Spacing and sizes are derived estimates, not original design source.

## Responsive behavior
Reference application crop is 1394 × 855 image pixels. Use a matching 1394 × 855 CSS-pixel test viewport at scale 1 as the initial baseline. Left navigation approximately 172 px, right rail 253 px, gaps 13–14 px. Center column flexes.
At smaller widths, preserve the map and move inspector/navigation to drawers; retain selection and camera. Verify 1280 × 800 and 200% browser zoom. Proposed responsive behavior is not shown in the reference.

## Accessibility and motion
Provide semantic list/tree alternatives, visible focus and keyboard selection. Color is not the only proof/status signal. Target text contrast 4.5:1, large text 3:1. Reduce motion removes ambient particle animation and camera flights. Preserve the neon identity without excessive glare or unreadable labels.

## Acceptance
Compare implementation with the application crop at the same scale, fixture, font environment and viewport. Capture side-by-side and overlay/diff evidence; document masking of genuinely dynamic regions.
Proposed tolerances: major regions within 2% of reference crop dimensions, spacing within 4 CSS px where measurable, no unexplained changes in palette, hierarchy or graph arrangement. Human visual review remains necessary; an image hash verifies the reference, not a faithful implementation.
RA-001 is complete for reference intake. RA-004 still needs implementation and visual acceptance.
