# Design

## System

CampusQA is a focused product UI. It uses a restrained light interface by default because the primary use case is reading and composing knowledge-base answers in classrooms, offices, and study spaces. Dark mode follows the user's system preference.

## Color

Use OKLCH tokens in `src/style.css`.

- Background: neutral gray with a slight violet cast, not cream or pure white.
- Surface: white and elevated white for the app shell, sidebar, input dock, and menus.
- Ink: high-contrast near-navy for primary text, softened neutral for secondary text.
- Brand: violet from the existing favicon for primary action and current mode.
- Accent: cyan for system indicators and supporting status.
- Semantic: red for errors, green for successful operations, amber for warnings.

## Typography

Use the system UI stack for product familiarity and performance. Type is fixed-scale, not viewport-fluid: compact labels, readable 15px chat text, and restrained headings.

## Layout

The app is a full-height product shell:

- Desktop: fixed history rail on the left, top command bar, central conversation canvas, sticky input dock.
- Mobile: history rail becomes an overlay drawer, content stays full-width, input controls wrap safely.
- Conversation width is capped for reading, while the shell keeps operational controls visible.

## Components

- Header: product identity, mode toggle, knowledge settings, and session state.
- Sidebar: session list with current selection, counts, timestamps, and delete actions.
- Messages: assistant responses are document-like panels; user messages are compact right-aligned bubbles.
- Composer: upload, clear, textarea, and send controls with stable icon button sizes.
- Toasts: variant-aware success/error feedback.

## Motion

Motion is limited to state feedback: drawer entrance, hover transitions, toast transitions, and loading dots. Reduced motion disables nonessential transitions.
