# Tauri Data Logger UI

A minimal desktop sample built with **React**, **Tailwind CSS** and
[Tauri](https://tauri.app).  The React interface talks to a small Python
backend through a Rust command so you can see how all the pieces fit together.

## Prerequisites

The project relies on tools from both the JavaScript and Rust ecosystems.

### Common tools

- [Node.js](https://nodejs.org/) 16 or newer
- [Rust](https://www.rust-lang.org/tools/install) via `rustup`
- Tauri CLI – install globally with `npm install -g @tauri-apps/cli` or use the
  `tauri` binary provided by the `npm` scripts

### Platform packages

**Windows**

- Install the [Visual Studio C++ Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe)
  and enable the *Desktop development with C++* workload.

**macOS**

- Install the Xcode command line tools: `xcode-select --install`

**Linux (Debian/Ubuntu)**

Install a handful of system libraries required by Tauri:

```bash
sudo apt update
sudo apt install libgtk-3-dev libwebkit2gtk-4.0-dev libsoup2.4-dev \
    libayatana-appindicator3-dev librsvg2-dev
```

Some modern distributions only provide WebKit 4.1.  In that case install
`libwebkit2gtk-4.1-dev` and run the commands below with
`TAURI_BUILD_FLAGS="--features wry/webkit2gtk_4_1"`.

## Setup and development

```bash
cd experiments/tauri_data_logger
npm install                           # fetch JavaScript dependencies
npm run tauri dev                     # start the Tauri development server
# If using WebKit 4.1:
# TAURI_BUILD_FLAGS="--features wry/webkit2gtk_4_1" npm run tauri dev
```

A desktop window opens.  Press **Load Logs from Python** to invoke the Python
script and display a few generated entries.

## Building a release binary

```bash
npm run build                         # bundle the React frontend
npm run tauri build                   # create a native executable
# or, with WebKit 4.1:
# TAURI_BUILD_FLAGS="--features wry/webkit2gtk_4_1" npm run tauri build
```

The compiled application will be placed in `src-tauri/target/release` (or
`debug` when running the dev command).

This example lives entirely inside `experiments/tauri_data_logger` and is
separate from the main Python plotting tools.

