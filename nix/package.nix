{
  lib,
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "ortec-cli";
  version = "0.1.0";
  pyproject = true;

  src = lib.cleanSource ../.;

  build-system = [ python3Packages.hatchling ];

  dependencies = with python3Packages; [
    httpx
    mcp
  ];

  nativeCheckInputs = [ python3Packages.pytestCheckHook ];

  pythonImportsCheck = [
    "ortec"
    "ortec.server"
  ];

  meta = {
    description = "CLI and MCP server for ORTEC Employee Self Service: shifts, swaps and leave";
    license = lib.licenses.mit;
    mainProgram = "ortec";
    platforms = lib.platforms.all;
  };
}
