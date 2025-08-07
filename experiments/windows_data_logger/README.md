# Windows Data Logger

Simple WinForms application that logs serial data to a text file. It mirrors the
features of the PyQt6 logger: choose log directory, optional sub-folder creation
based on the file name, and customizable file names.

## Building

1. Install the [.NET 6 SDK](https://dotnet.microsoft.com/en-us/download).
2. From a developer command prompt, run:
   ```sh
   dotnet build
   ```

## Running

After building, launch the app with:

```sh
dotnet run
```

Select the serial port and baud rate, then enter a command. Choose a log
directory, specify a base file name, and enable **Subfolder** to create a folder
from the file name (all words except the last). Press **Record** to start
logging and **Cancel** to stop.
