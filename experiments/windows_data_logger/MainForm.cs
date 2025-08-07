using System;
using System.IO;
using System.IO.Ports;
using System.Windows.Forms;

namespace WindowsDataLogger
{
    public class MainForm : Form
    {
        private readonly ComboBox portBox = new();
        private readonly ComboBox baudBox = new();
        private readonly Button connectBtn = new();
        private readonly TextBox cmdBox = new();
        private readonly Button sendBtn = new();
        private readonly TextBox dirBox = new();
        private readonly Button browseBtn = new();
        private readonly TextBox fileBox = new();
        private readonly CheckBox subdirCheck = new();
        private readonly NumericUpDown sampleCount = new();
        private readonly Button recordBtn = new();
        private readonly Button cancelBtn = new();
        private readonly ProgressBar progress = new();
        private readonly SerialPort serial = new();
        private StreamWriter? logFile;
        private int sampleIdx = 0;

        public MainForm()
        {
            Text = "Data Logger";
            Width = 640;
            Height = 260;

            // Port selection
            portBox.Left = 10; portBox.Top = 10; portBox.Width = 120;
            portBox.Items.AddRange(SerialPort.GetPortNames());
            if (portBox.Items.Count > 0) portBox.SelectedIndex = 0;

            baudBox.Left = 140; baudBox.Top = 10; baudBox.Width = 80;
            baudBox.Items.AddRange(new object[] { "115200", "9600", "57600" });
            baudBox.SelectedIndex = 0;

            connectBtn.Left = 230; connectBtn.Top = 10; connectBtn.Text = "Connect";
            connectBtn.Click += (s, e) => ToggleConnection();

            // Command widgets
            cmdBox.Left = 10; cmdBox.Top = 40; cmdBox.Width = 200; cmdBox.Text = ">2050;1270;1;";
            sendBtn.Left = 220; sendBtn.Top = 38; sendBtn.Text = "Send";
            sendBtn.Click += (s, e) => { if (serial.IsOpen) serial.WriteLine(cmdBox.Text); };

            // Directory selection
            dirBox.Left = 10; dirBox.Top = 80; dirBox.Width = 200;
            dirBox.Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "python_plot_logs");
            browseBtn.Left = 220; browseBtn.Top = 78; browseBtn.Text = "Browse";
            browseBtn.Click += (s, e) => ChooseDir();

            fileBox.Left = 10; fileBox.Top = 110; fileBox.Width = 200; fileBox.Text = "log";
            subdirCheck.Left = 220; subdirCheck.Top = 110; subdirCheck.Text = "Subfolder";

            sampleCount.Left = 10; sampleCount.Top = 140; sampleCount.Width = 80; sampleCount.Value = 2000;
            recordBtn.Left = 100; recordBtn.Top = 140; recordBtn.Text = "Record";
            recordBtn.Click += (s, e) => StartLogging();
            cancelBtn.Left = 180; cancelBtn.Top = 140; cancelBtn.Text = "Cancel"; cancelBtn.Enabled = false;
            cancelBtn.Click += (s, e) => CancelLogging();

            progress.Left = 10; progress.Top = 170; progress.Width = 300;

            Controls.AddRange(new Control[] { portBox, baudBox, connectBtn, cmdBox, sendBtn, dirBox, browseBtn, fileBox, subdirCheck, sampleCount, recordBtn, cancelBtn, progress });

            serial.DataReceived += OnData;
        }

        private void ToggleConnection()
        {
            if (serial.IsOpen)
            {
                serial.Close();
                connectBtn.Text = "Connect";
                return;
            }
            serial.PortName = portBox.SelectedItem?.ToString() ?? string.Empty;
            serial.BaudRate = int.Parse(baudBox.SelectedItem?.ToString() ?? "115200");
            serial.Open();
            connectBtn.Text = "Disconnect";
        }

        private void ChooseDir()
        {
            using var dialog = new FolderBrowserDialog();
            if (dialog.ShowDialog() == DialogResult.OK)
            {
                dirBox.Text = dialog.SelectedPath;
            }
        }

        private void StartLogging()
        {
            var baseName = fileBox.Text;
            var dir = dirBox.Text;
            var fullDir = dir;
            if (subdirCheck.Checked)
            {
                var parts = baseName.Split(' ');
                if (parts.Length > 1)
                {
                    var folder = string.Join(" ", parts, 0, parts.Length - 1);
                    fullDir = Path.Combine(dir, folder);
                }
            }
            Directory.CreateDirectory(fullDir);
            var path = Path.Combine(fullDir, baseName + ".txt");
            logFile = new StreamWriter(path);
            sampleIdx = 0;
            progress.Value = 0;
            progress.Maximum = (int)sampleCount.Value;
            cancelBtn.Enabled = true;
            recordBtn.Enabled = false;
        }

        private void OnData(object? sender, SerialDataReceivedEventArgs e)
        {
            if (logFile == null) return;
            var line = serial.ReadLine();
            if (line.StartsWith(">")) line = line[1..];
            logFile.WriteLine(line);
            sampleIdx++;
            BeginInvoke(new Action(() =>
            {
                progress.Value = Math.Min(progress.Maximum, sampleIdx);
                if (sampleIdx >= progress.Maximum) CancelLogging();
            }));
        }

        private void CancelLogging()
        {
            logFile?.Dispose();
            logFile = null;
            recordBtn.Enabled = true;
            cancelBtn.Enabled = false;
        }
    }
}
