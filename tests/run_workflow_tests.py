#!/usr/bin/env python3
"""
Test runner for workflow monitoring system tests.

This script provides a convenient way to run all workflow monitoring tests
with proper configuration and reporting.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def run_tests(test_files=None, verbose=False, coverage=False, html_report=False):
    """
    Run workflow monitoring tests.

    Args:
        test_files: List of test files to run (None for all)
        verbose: Enable verbose output
        coverage: Enable coverage reporting
        html_report: Generate HTML coverage report
    """
    # Base pytest command
    cmd = ["python", "-m", "pytest"]

    # Add test files if specified
    if test_files:
        cmd.extend(test_files)
    else:
        # Run all workflow monitoring tests
        # testpaths in pyproject.toml already points pytest at tests/
        cmd.append("tests")

    # Add verbosity
    if verbose:
        cmd.append("-v")

    # Add coverage if requested
    if coverage:
        cmd.extend(
            [
                "--cov=app.services.workflow_service",
                "--cov=app.services.websocket_manager",
                "--cov=app.api.routes.workflow_router",
                "--cov-report=term-missing",
            ]
        )

        if html_report:
            cmd.append("--cov-report=html:htmlcov")

    # Add other useful options
    cmd.extend(
        [
            "--tb=short",  # Short traceback format
            "--strict-markers",  # Strict marker checking
            "--disable-warnings",  # Disable warnings for cleaner output
        ]
    )

    print("Running workflow monitoring tests...")
    print(f"Command: {' '.join(cmd)}")
    print("-" * 50)

    try:
        result = subprocess.run(cmd, cwd=project_root, check=True)
        print("\n" + "=" * 50)
        print("✅ All tests passed successfully!")

        if html_report and coverage:
            print(
                f"📊 HTML coverage report generated in: {project_root}/htmlcov/index.html"
            )

        return 0

    except subprocess.CalledProcessError as e:
        print("\n" + "=" * 50)
        print("❌ Some tests failed!")
        return e.returncode
    except FileNotFoundError:
        print("❌ Error: pytest not found. Please install it with: pip install pytest")
        return 1


def main():
    """Main entry point for the test runner."""
    parser = argparse.ArgumentParser(
        description="Run workflow monitoring system tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_workflow_tests.py                    # Run all tests
  python run_workflow_tests.py -v                 # Run with verbose output
  python run_workflow_tests.py --coverage         # Run with coverage
  python run_workflow_tests.py --coverage --html  # Run with HTML coverage report
  python run_workflow_tests.py test_workflow_monitoring.py  # Run specific test file
        """,
    )

    parser.add_argument(
        "test_files",
        nargs="*",
        help="Specific test files to run (default: all workflow tests)",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )

    parser.add_argument(
        "--coverage", action="store_true", help="Enable coverage reporting"
    )

    parser.add_argument(
        "--html",
        action="store_true",
        help="Generate HTML coverage report (requires --coverage)",
    )

    parser.add_argument(
        "--list-tests",
        action="store_true",
        help="List all available tests without running them",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.html and not args.coverage:
        print("❌ Error: --html requires --coverage")
        return 1

    # List tests if requested
    if args.list_tests:
        cmd = ["python", "-m", "pytest", "--collect-only", "-q"]
        if args.test_files:
            cmd.extend(args.test_files)
        else:
            cmd.append("tests")

        try:
            subprocess.run(cmd, cwd=project_root, check=True)
        except subprocess.CalledProcessError:
            return 1
        return 0

    # Run tests
    return run_tests(
        test_files=args.test_files,
        verbose=args.verbose,
        coverage=args.coverage,
        html_report=args.html,
    )


if __name__ == "__main__":
    sys.exit(main())
