# Tauri Data Logger UI

A minimal sample data logger UI built with React, Tailwind CSS, and Tauri. The app invokes a Rust command to fetch sample log entries and displays them in a small React interface.

## Prerequisites

- [Node.js](https://nodejs.org/) (v16 or later)
- [Rust](https://www.rust-lang.org/tools/install)
- Tauri CLI: `npm install -g @tauri-apps/cli` (or run through `npm` scripts)

## Setup

```bash
cd ui_examples/tauri_data_logger
npm install
npm run tauri dev
```

The app opens a desktop window. Use the **Load Sample Logs** button to fetch a few hard-coded log entries from the Rust backend.

## Build

To create a production build:

```bash
npm run build
npm run tauri build
```

This produces a standalone binary in the `src-tauri/target` directory.
