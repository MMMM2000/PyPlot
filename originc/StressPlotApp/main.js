// Collect values from the HTML form and invoke a LabTalk script to run the
// stress dependence plotter.  The LabTalk script (run_stress.ogs) will read
// global variables that we set here and then call the Python back‑end.  Paths
// are escaped for use in LabTalk.

function escapePath(p) {
  // Replace single backslash with double backslash for LabTalk strings
  return p ? p.replace(/\\/g, "\\\\") : '';
}

function runPlot() {
  const lt = window.external;
  if (!lt || !lt.LT_execute) {
    alert('Origin LabTalk interface is not available. Please ensure this dialog is running inside Origin.');
    return;
  }
  // Get selected files
  const filesElem = document.getElementById('files');
  const files = filesElem.files;
  if (!files || files.length === 0) {
    alert('Please select at least one measurement file.');
    return;
  }
  let filePaths = [];
  for (let i = 0; i < files.length; i++) {
    // Browser security hides full paths; Origin modifies the file input to
    // return the full path in the .value property.  Use .value or fallback to
    // .name if necessary.  Escape backslashes for LabTalk.
    const path = files[i].value || files[i].name;
    filePaths.push(escapePath(path));
  }
  // Build variable list based on checkboxes
  const vars = [];
  if (document.getElementById('var_sum').checked) vars.push('sum');
  if (document.getElementById('var_dT').checked) vars.push('dT');
  if (document.getElementById('var_T1').checked) vars.push('T1');
  if (document.getElementById('var_T2').checked) vars.push('T2');
  if (vars.length === 0) {
    alert('Please select at least one variable to plot.');
    return;
  }
  const baseline = document.querySelector('input[name="baseline"]:checked').value;
  const show = document.getElementById('show_plots').checked ? 1 : 0;
  const save = document.getElementById('save_plots').checked ? 1 : 0;
  const outdir = escapePath(document.getElementById('output_dir').value.trim());
  const otp = escapePath(document.getElementById('otp_file').value);
  const png = escapePath(document.getElementById('png_file').value);

  try {
    // Set global LabTalk variables.  Use double quotes around strings.
    lt.LT_execute(`__files$="${filePaths.join('|')}";`);
    lt.LT_execute(`__vars$="${vars.join(',')}";`);
    lt.LT_execute(`__baseline$="${baseline}";`);
    lt.LT_execute(`__show$=${show};`);
    lt.LT_execute(`__save$=${save};`);
    lt.LT_execute(`__outdir$="${outdir}";`);
    lt.LT_execute(`__otp$="${otp}";`);
    lt.LT_execute(`__png$="${png}";`);
    // Run the LabTalk section that will drive Python and Origin C.  The path
    // is relative to the app folder; Origin resolves it using the app's
    // working directory.  Adjust if you rename the folder.
    lt.LT_execute('run.section("StressPlotApp\\run_stress.ogs", Main);');
  } catch (e) {
    alert('Error executing LabTalk: ' + e);
  }
}