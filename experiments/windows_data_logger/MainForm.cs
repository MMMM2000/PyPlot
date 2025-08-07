using System;
using System.IO;
using System.IO.Ports;
using System.Windows.Forms;

namespace WindowsDataLogger;

public class MainForm : Form
{
    private readonly ComboBox _comboPorts;
    private readonly ComboBox _comboBaud;
    private readonly Button _btnConnect;
    private readonly Button _btnSend;
    private readonly TextBox _txtCommand;
    private readonly TextBox _txtDir;
    private readonly TextBox _txtFile;
    private readonly Button _btnRecord;
    private readonly Button _btnCancel;
    private readonly ProgressBar _progress;
    private readonly NumericUpDown _numSamples;
    private readonly Label _lblResponse;
    private readonly Label _lblTime;

    private SerialPort? _serial;
    private StreamWriter? _logFile;
    private int _sampleCount;
    private int _sampleIdx;
    private double? _sampleRate;
    private DateTime? _lastSampleTime;

    private readonly System.Windows.Forms.Timer _timeTimer;

    private const string DEFAULT_COMMAND = ">2050;1270;1;";
    private const string DEFAULT_FILE = "FeSiBP 156_2 s2-1a 74mA 2,5a";
    private static readonly string DEFAULT_DIR = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "python_plot_logs");

    public MainForm()
    {
        Text = "Data Logger";
        Width = 650;
        Height = 400;

        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 6, ColumnCount = 1 };
        Controls.Add(layout);

        // Top row: port selection
        var portPanel = new FlowLayoutPanel { Dock = DockStyle.Fill };
        _comboPorts = new ComboBox { Width = 100 };
        _comboBaud = new ComboBox { Width = 80 };
        _comboBaud.Items.AddRange(new object[] { "9600", "19200", "38400", "57600", "115200" });
        _comboBaud.SelectedIndex = 0;
        var btnRefresh = new Button { Text = "Refresh" };
        btnRefresh.Click += (_, _) => PopulatePorts();
        _btnConnect = new Button { Text = "Connect" };
        _btnConnect.Click += ToggleConnection;
        portPanel.Controls.AddRange(new Control[] {
            new Label{ Text = "Port:" }, _comboPorts,
            new Label{ Text = "Baud:" }, _comboBaud,
            btnRefresh, _btnConnect
        });
        layout.Controls.Add(portPanel);

        // Command row
        var cmdPanel = new FlowLayoutPanel { Dock = DockStyle.Fill };
        _txtCommand = new TextBox { Text = DEFAULT_COMMAND, Width = 250 };
        _btnSend = new Button { Text = "Send", Enabled = false };
        _btnSend.Click += SendCommand;
        cmdPanel.Controls.AddRange(new Control[] {
            new Label{ Text = "Command:" }, _txtCommand, _btnSend
        });
        layout.Controls.Add(cmdPanel);

        // Directory row
        var dirPanel = new FlowLayoutPanel { Dock = DockStyle.Fill };
        _txtDir = new TextBox { Text = DEFAULT_DIR, Width = 300 };
        var btnBrowse = new Button { Text = "Browse" };
        btnBrowse.Click += (_, _) => ChooseDir();
        dirPanel.Controls.AddRange(new Control[] {
            new Label{ Text = "Log Dir:" }, _txtDir, btnBrowse
        });
        layout.Controls.Add(dirPanel);

        // File/sample row
        var filePanel = new FlowLayoutPanel { Dock = DockStyle.Fill };
        _txtFile = new TextBox { Text = DEFAULT_FILE, Width = 200 };
        _numSamples = new NumericUpDown { Minimum = 1, Maximum = 1000000, Value = 2000 };
        _btnRecord = new Button { Text = "Record" };
        _btnRecord.Click += StartLogging;
        _btnCancel = new Button { Text = "Cancel", Enabled = false };
        _btnCancel.Click += CancelLogging;
        filePanel.Controls.AddRange(new Control[] {
            new Label{ Text = "File:" }, _txtFile,
            new Label{ Text = "Samples:" }, _numSamples,
            _btnRecord, _btnCancel
        });
        layout.Controls.Add(filePanel);

        // Response label
        _lblResponse = new Label { AutoSize = true };
        layout.Controls.Add(_lblResponse);

        // Progress bar
        _progress = new ProgressBar { Minimum = 0, Maximum = 2000, Dock = DockStyle.Fill };
        layout.Controls.Add(_progress);

        // Time label
        _lblTime = new Label { Text = "Time remaining: N/A", AutoSize = true };
        layout.Controls.Add(_lblTime);

        Directory.CreateDirectory(DEFAULT_DIR);
        PopulatePorts();

        _timeTimer = new System.Windows.Forms.Timer { Interval = 1000 };
        _timeTimer.Tick += UpdateTimeEstimate;
        _timeTimer.Start();
    }

    private void PopulatePorts()
    {
        _comboPorts.Items.Clear();
        foreach (var name in SerialPort.GetPortNames())
            _comboPorts.Items.Add(name);
        if (_comboPorts.Items.Count > 0)
            _comboPorts.SelectedIndex = 0;
    }

    private void ToggleConnection(object? sender, EventArgs e)
    {
        try
        {
            if (_serial == null || !_serial.IsOpen)
            {
                if (_comboPorts.SelectedItem == null) return;
                _serial = new SerialPort(_comboPorts.SelectedItem.ToString()!, int.Parse(_comboBaud.SelectedItem?.ToString() ?? "9600"));
                _serial.NewLine = "\n";
                _serial.DataReceived += SerialDataReceived;
                _serial.Open();
                _btnConnect.Text = "Disconnect";
                _btnSend.Enabled = true;
            }
            else
            {
                _serial.Close();
                _btnConnect.Text = "Connect";
                _btnSend.Enabled = false;
            }
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Error");
        }
    }

    private void SendCommand(object? sender, EventArgs e)
    {
        try
        {
            _serial?.WriteLine(_txtCommand.Text);
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Error");
        }
    }

    private void ChooseDir()
    {
        using var dlg = new FolderBrowserDialog { SelectedPath = _txtDir.Text };
        if (dlg.ShowDialog() == DialogResult.OK)
            _txtDir.Text = dlg.SelectedPath;
    }

    private void StartLogging(object? sender, EventArgs e)
    {
        using var dlg = new SaveFileDialog
        {
            InitialDirectory = _txtDir.Text,
            FileName = _txtFile.Text + ".txt",
            Filter = "Text files (*.txt)|*.txt"
        };
        if (dlg.ShowDialog() != DialogResult.OK)
            return;

        try
        {
            _logFile = new StreamWriter(dlg.FileName);
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Error");
            return;
        }

        _sampleCount = (int)_numSamples.Value;
        _sampleIdx = 0;
        _progress.Maximum = _sampleCount;
        _progress.Value = 0;
        _btnRecord.Enabled = false;
        _btnCancel.Enabled = true;
        _sampleRate = null;
        _lastSampleTime = null;
    }

    private void CancelLogging(object? sender, EventArgs e)
    {
        _logFile?.Dispose();
        _logFile = null;
        _btnRecord.Enabled = true;
        _btnCancel.Enabled = false;
        _sampleIdx = 0;
        _sampleRate = null;
        UpdateTimeEstimate(null, EventArgs.Empty);
    }

    private void SerialDataReceived(object? sender, SerialDataReceivedEventArgs e)
    {
        try
        {
            string line = _serial!.ReadLine();
            var now = DateTime.Now;
            if (_lastSampleTime.HasValue)
            {
                var dt = (now - _lastSampleTime.Value).TotalSeconds;
                if (dt > 0)
                    _sampleRate = 1.0 / dt;
            }
            _lastSampleTime = now;

            BeginInvoke(new Action(() =>
            {
                _lblResponse.Text = line;
                if (_logFile != null)
                {
                    _logFile.Write(line.TrimStart('>'));
                    _sampleIdx++;
                    if (_sampleIdx <= _progress.Maximum)
                        _progress.Value = _sampleIdx;
                    if (_sampleIdx >= _sampleCount)
                        CancelLogging(null, EventArgs.Empty);
                }
            }));
        }
        catch (Exception ex)
        {
            BeginInvoke(new Action(() => MessageBox.Show(ex.Message, "Error")));
        }
    }

    private void UpdateTimeEstimate(object? sender, EventArgs e)
    {
        if (_logFile != null && _sampleRate.HasValue)
        {
            int remaining = _sampleCount - _sampleIdx;
            int secs = (int)Math.Ceiling(remaining / _sampleRate.Value);
            _lblTime.Text = $"Time remaining: {secs}s";
        }
        else
        {
            _lblTime.Text = "Time remaining: N/A";
        }
    }
}

