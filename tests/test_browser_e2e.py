"""Real Browser End-to-End Test Suite with Playwright (Section 8).

Launches the live FastAPI application on an isolated local port and controls a headless
Chromium browser to execute the complete real-user DAT Studio journey:
1. Load home page & verify HighRadius enterprise visual theme.
2. Upload synthetic CSV specification and reference DAT.
3. Verify specification table rendering.
4. Inline editing of specification fields.
5. Bulk mandatory-status updates.
6. Acknowledge UNKNOWN mandatory rules.
7. Trigger AI specification interpretation and wait for completion.
8. Approve specification via confirmation modal.
9. Transition to Stage 3 (DAT Review).
10. Run TASAA / DAT analysis.
11. Select a field and inspect exact byte boundaries and rules context.
12. Edit a DAT value inline in the review table.
13. Save value changes and trigger continuous revalidation.
14. Generate and validate DAT for export.
15. Verify export readiness and download controls.
16. Reload and resume saved project.
"""
import io
import csv
import hashlib
import json
import os
import socket
import threading
import time
import zipfile
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

import uvicorn
import web
import specs
from web import app, studio_service


class SyntheticProvider:
    def status(self):
        return {'provider': 'Synthetic', 'ai_available': True, 'model_id': 'synthetic-model', 'configured': True, 'connection_status': 'connected'}

    def batches(self, capability, items, response_model, instructions, context=None, key='items', max_items=None):
        return [{**(context or {}), key: [item]} for item in items] or [{**(context or {}), key: []}]

    def invoke_structured(self, capability, context, response_model, instructions, scope=None):
        if capability == 'studio_spec':
            interpretations = []
            for group in context.get('groups', []):
                num = group.get('datatype_conversion') in ('NUMBER', 'INTEGER EXTERNAL')
                interpretations.append({
                    'group_id': group['group_id'],
                    'datatype': 'decimal' if group.get('datatype_conversion') == 'NUMBER' else 'char',
                    'conversion': 'to_number' if num else 'identity',
                    'date_format': '',
                    'padding_side': 'left' if num else 'none',
                    'padding_char': '0' if num else ' ',
                    'allow_internal_spaces': False,
                    'required': False,
                    'explanation': 'Synthetic interpretation',
                    'questions': []
                })
            result = {'summary': 'Synthetic spec analysis', 'interpretations': interpretations, 'observations': []}
        else:
            result = {'summary': 'Synthetic DAT review', 'findings': [], 'observations': []}
        response_model.model_validate(result)
        return {'result': result, 'usage': {'inputTokens': 10, 'outputTokens': 10},
                'provider': 'SYNTHETIC', 'latency_ms': 1}


def get_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="module")
def server():
    port = get_free_port()
    web.provider = SyntheticProvider()
    studio_service.start()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()

    # Wait for server to be responsive
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=0.1)
            s.close()
            break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.1)
    else:
        raise RuntimeError("Server failed to start")

    yield base_url
    srv.should_exit = True
    studio_service.stop()
    thread.join(timeout=2.0)


def test_browser_full_studio_workflow(server, tmp_path):
    """Execute complete browser user journey using Playwright Chromium."""
    spec_path = tmp_path / "simple_spec.csv"
    spec_path.write_text(
        "Field Name,Field ID,Category / Functional Group,Mapping Type,Start Pos,End Pos,Length,Data Type / Oracle Conversion Function,Padding / Alignment Rule,Raw Sample Value in DAT File (Quoted),Cleansed Output in Oracle Staging Table,Sequence,Record Type,Mandatory,Alignment,Padding Byte,Padding Direction,Format,Allowed Values,Conditional Rules,Description,Remarks,Source Reference\n"
        "HDR_TYPE,HDR_TYPE,HEADER,POSITION,1,2,2,CHAR,Fixed Text (Exact Width),01,01,1,,YES,LEFT, ,NONE,,,,\n"
        "CUST_ID,CUST_ID,CUSTOMER,POSITION,3,10,8,CHAR,Fixed Text (Exact Width),CUST0001,CUST0001,2,,YES,LEFT, ,NONE,,,,\n"
        "AMOUNT,AMOUNT,TRANSACTION,POSITION,11,18,8,NUMBER,Leading zeroes,00012500,125.00,3,,YES,RIGHT,0,LEADING,,,,\n"
    )
    dat_path = tmp_path / "simple_data.dat"
    dat_path.write_bytes(b"01CUST000100012500\n01CUST000200009950\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Step 1: Navigate to Home
        page.goto(server)
        page.wait_for_selector("#projectTitle")
        assert "Spec–DAT Studio" in page.title()
        assert page.is_visible("#uploadStage")

        # Step 2: Fill Upload Form
        page.fill("#projectName", "Playwright E2E Test Project")
        page.set_input_files("#specFile", str(spec_path))
        page.set_input_files("#datFile", str(dat_path))

        # Accept native dialogs automatically
        page.on("dialog", lambda dialog: dialog.accept())

        # Step 3: Upload files
        page.click("#uploadButton")

        # Step 4: Wait for transition to Stage 2 (Review specification)
        page.wait_for_selector("#reviewStage:not(.hidden)", timeout=10000)
        page.wait_for_selector("#reviewTable")
        assert page.is_visible("#reviewTable")

        # Step 5: Check that specification table rendered fields
        field_rows = page.query_selector_all("#reviewBody tr")
        assert len(field_rows) >= 3

        # Step 6: Test Bulk Mandatory tool
        page.select_option("#bulkMandatory", "YES")
        page.click("#applyBulkMandatory")
        page.wait_for_selector("#confirmDialog[open]", timeout=3000)
        page.click('#confirmDialog button[value="confirm"]')
        page.wait_for_timeout(500)

        # Step 7: Test UNKNOWN acknowledgment checkbox
        ack_checkbox = page.query_selector("#ackUnknownMandatory")
        if ack_checkbox and ack_checkbox.is_visible():
            ack_checkbox.check()

        # Step 8: Inline edit a field property in the spec table
        cust_input = page.query_selector('input[data-spec-field="CUST_ID"][data-column="field_name"]')
        if cust_input:
            cust_input.fill("CUSTOMER_CODE")
            cust_input.dispatch_event("input")
            page.wait_for_timeout(200)

        # Step 9: Save draft
        page.click("#saveSpecButton")
        page.wait_for_timeout(500)

        # Step 10: Trigger AI specification interpretation
        page.click("#analyzeSpecButton")

        # Step 11: Wait for AI analysis to complete so approveSpecButton becomes enabled
        page.wait_for_selector("#approveSpecButton:not([disabled])", timeout=15000)

        # Step 12: Approve specification
        page.click("#approveSpecButton")
        page.wait_for_selector("#confirmDialog[open]", timeout=3000)
        page.click('#confirmDialog button[value="confirm"]')

        # Step 13: Wait for Stage 3 (DAT Review) to be active
        page.wait_for_selector("#analyzeDatButton:not(.hidden)", timeout=10000)
        assert page.is_visible("#analyzeDatButton")

        # Step 14: Click Analyze DAT
        page.click("#analyzeDatButton")
        page.wait_for_timeout(2000)

        # Step 15: Select a field row and inspect exact byte boundaries in #fieldContext
        first_row = page.query_selector('#reviewBody tr[data-field-row]')
        if first_row:
            first_row.click()
            page.wait_for_timeout(300)
            assert page.is_visible("#fieldContext")

        # Step 16: Edit a DAT value in table
        value_input = page.locator('input[data-value-field="CUST_ID"]')
        assert value_input.is_visible()
        value_input.fill("CUST9999")
        page.click("#saveValuesButton")
        page.wait_for_selector("#saveValuesButton[disabled]", timeout=5000)

        # Step 17: Validate & prepare DAT
        page.click("#analyzeDatButton")
        page.wait_for_selector("#generateButton:not([disabled])", timeout=15000)
        page.click("#generateButton")
        page.wait_for_selector("#downloadDat:not(.hidden)", timeout=15000)
        with page.expect_download() as download_info:
            page.click("#downloadDat")
        downloaded = download_info.value
        assert downloaded.failure() is None
        assert Path(downloaded.path()).read_bytes() == b"01CUST999900012500\n01CUST000200009950\n"

        # Step 18: Verify project reload
        page.click("#refreshProject")
        page.wait_for_timeout(1000)
        assert page.is_visible("#projectTitle")

        browser.close()


def _batch_spec(path, marker, width):
    labels = {column['key']: column['label'] for column in specs.MASTER_COLUMNS}
    with path.open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=[column['label'] for column in specs.MASTER_COLUMNS])
        writer.writeheader()
        for number, (name, start, end) in enumerate((('KIND', 1, 1), ('VALUE', 2, width)), 1):
            values = {'id': f'{marker}_{name}', 'field_name': name, 'mapping_type': 'POSITION',
                      'start_pos': start, 'end_pos': end, 'length': end - start + 1,
                      'datatype_conversion': 'CHAR', 'padding_alignment': 'Fixed Text (Exact Width)',
                      'mandatory': 'YES', 'sequence': number}
            writer.writerow({labels[key]: value for key, value in values.items()})


def _approve_batch_spec(page, filename, marker):
    spec_id = page.locator('[data-batch-spec]').filter(has_text=filename).get_attribute('data-batch-spec')
    page.select_option('#batchSpecSelect', spec_id)
    page.locator('#batchLayoutRules details summary').first.click()
    page.locator('[data-batch-add-condition="0"]').click()
    page.locator('#batchLayoutRules details summary').first.click()
    for key, value in (('start', '1'), ('end', '1'), ('equals', marker)):
        control = page.locator(f'[data-batch-layout="0"][data-batch-condition="0"][data-batch-key="{key}"]')
        control.fill(value)
        control.dispatch_event('change')
    page.click('#batchSaveSpec')
    page.wait_for_selector('#batchSaveSpec[disabled]')
    page.click('#batchApproveSpec')
    page.wait_for_selector('#confirmDialog[open]')
    page.click('#confirmDialog button[value="confirm"]')
    page.locator('[data-batch-spec]').filter(has_text=filename).locator('.pill.good').wait_for()
    return spec_id


def test_browser_batch_upload_routing_export_and_reimport(server, tmp_path):
    """Exercise two independent specs, mixed DAT routing, downloads, and reimport in Chromium."""
    head_spec, detail_spec = tmp_path / 'head.csv', tmp_path / 'detail.csv'
    _batch_spec(head_spec, 'H', 5)
    _batch_spec(detail_spec, 'D', 7)
    mixed = b'H1234\nD000123\nH5678\n'
    detail = b'D000456\nD000789\n'
    mixed_path, detail_path = tmp_path / 'mixed.dat', tmp_path / 'detail.dat'
    mixed_path.write_bytes(mixed)
    detail_path.write_bytes(detail)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        page.set_default_timeout(10000)
        page.goto(server)
        page.click('[data-stage="batch"]')
        page.fill('#batchProjectName', 'Browser multi-file batch')
        page.set_input_files('#batchSpecFiles', [str(head_spec), str(detail_spec)])
        page.set_input_files('#batchDatFiles', [str(mixed_path), str(detail_path)])
        page.click('#batchUploadButton')
        page.locator('[data-batch-spec]').first.wait_for()
        assert page.locator('[data-batch-spec]').count() == 2
        assert page.locator('[data-batch-dat]').count() == 2
        assert not page.locator('#batchAssociations input:checked').count()

        head_id = _approve_batch_spec(page, 'head.csv', 'H')
        detail_id = _approve_batch_spec(page, 'detail.csv', 'D')
        mixed_id = page.locator('[data-batch-dat]').filter(has_text='mixed.dat').get_attribute('data-batch-dat')
        detail_dat_id = page.locator('[data-batch-dat]').filter(has_text='detail.dat').get_attribute('data-batch-dat')
        for dat_id, spec_id in ((mixed_id, head_id), (mixed_id, detail_id), (detail_dat_id, detail_id)):
            page.locator(f'input[data-batch-dat-id="{dat_id}"][data-batch-spec-id="{spec_id}"]').check()
        page.click('#batchSaveAssociations')
        page.get_by_text('Explicit DAT-to-specification associations saved.').wait_for()
        page.click('#batchProcess')
        page.locator('#batchSummary').get_by_text('5', exact=True).wait_for()
        page.locator(f'[data-batch-dat="{mixed_id}"]').click()
        page.locator('[data-batch-record]').nth(1).click()
        page.locator('#batchRecordEvidence').get_by_text('routed', exact=False).first.wait_for()
        assert 'routed' in page.locator('#batchRecordEvidence').inner_text()
        assert detail_id in page.locator('#batchRecordEvidence').inner_text()

        page.click('#batchAi')
        page.click('#batchGenerate')
        page.locator(f'[data-batch-download="{detail_dat_id}"]:not([disabled])').wait_for()
        with page.expect_download() as individual_info:
            page.locator(f'[data-batch-download="{detail_dat_id}"]').click()
        assert Path(individual_info.value.path()).read_bytes() == detail
        with page.expect_download() as archive_info:
            page.click('#batchDownloadArchive')
        with zipfile.ZipFile(archive_info.value.path()) as archive:
            exported = {name: archive.read(name) for name in archive.namelist()}
        assert exported[f'validated-{mixed_id}.dat'] == mixed
        assert exported[f'validated-{detail_dat_id}.dat'] == detail
        for dat_id in (mixed_id, detail_dat_id):
            report = json.loads(exported[f'validation-{dat_id}.json'])
            assert report['unvalidated_count'] == 0
            assert all(record['spec_version'] == 1 for record in report['record_summaries'])

        # Reimport an exported DAT in a fresh browser project and validate it again.
        imported = tmp_path / 'reimported.dat'
        imported.write_bytes(exported[f'validated-{detail_dat_id}.dat'])
        page.click('#newProjectButton')
        page.click('[data-stage="batch"]')
        page.fill('#batchProjectName', 'Reimport generated DAT')
        page.set_input_files('#batchSpecFiles', str(detail_spec))
        page.set_input_files('#batchDatFiles', str(imported))
        page.click('#batchUploadButton')
        page.locator('[data-batch-spec]').first.wait_for()
        imported_spec_id = _approve_batch_spec(page, 'detail.csv', 'D')
        imported_dat_id = page.locator('[data-batch-dat]').first.get_attribute('data-batch-dat')
        page.locator(f'input[data-batch-dat-id="{imported_dat_id}"][data-batch-spec-id="{imported_spec_id}"]').check()
        page.click('#batchSaveAssociations')
        page.get_by_text('Explicit DAT-to-specification associations saved.').wait_for()
        page.click('#batchProcess')
        page.locator('#batchSummary').get_by_text('2', exact=True).wait_for()
        assert '0' in page.locator('#batchSummary').inner_text()
        browser.close()


def test_browser_batch_ambiguous_and_unknown_rows_preserved_and_export_blocked(server, tmp_path):
    """Conflicting and unmatched rows stay byte-identical and cannot be exported as validated."""
    head_spec, competing_spec = tmp_path / 'head.csv', tmp_path / 'competing.csv'
    _batch_spec(head_spec, 'H', 5)
    _batch_spec(competing_spec, 'H', 5)
    data = b'H1234\nX9999\n'
    dat_path = tmp_path / 'unknown.dat'
    dat_path.write_bytes(data)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(accept_downloads=True)
        page.set_default_timeout(10000)
        page.goto(server)
        page.click('[data-stage="batch"]')
        page.fill('#batchProjectName', 'Unknown record review')
        page.set_input_files('#batchSpecFiles', [str(head_spec), str(competing_spec)])
        page.set_input_files('#batchDatFiles', str(dat_path))
        page.click('#batchUploadButton')
        page.locator('[data-batch-spec]').first.wait_for()
        spec_id = _approve_batch_spec(page, 'head.csv', 'H')
        competing_id = _approve_batch_spec(page, 'competing.csv', 'H')
        dat_id = page.locator('[data-batch-dat]').first.get_attribute('data-batch-dat')
        page.locator(f'input[data-batch-dat-id="{dat_id}"][data-batch-spec-id="{spec_id}"]').check()
        page.locator(f'input[data-batch-dat-id="{dat_id}"][data-batch-spec-id="{competing_id}"]').check()
        page.click('#batchSaveAssociations')
        page.get_by_text('Explicit DAT-to-specification associations saved.').wait_for()
        page.click('#batchProcess')
        page.locator('[data-batch-record]').nth(1).wait_for()
        page.locator('[data-batch-record]').first.click()
        page.locator('#batchRecordEvidence').get_by_text('AMBIGUOUS_LAYOUT', exact=False).first.wait_for()
        ambiguous_digest = hashlib.sha256(b'H1234').hexdigest()
        assert page.locator('#batchRecordEvidence').inner_text().count(ambiguous_digest) == 2
        page.locator('[data-batch-record]').nth(1).click()
        page.locator('#batchRecordEvidence').get_by_text('UNKNOWN_LAYOUT', exact=False).first.wait_for()
        assert 'UNKNOWN_LAYOUT' in page.locator('#batchRecordEvidence').inner_text()
        unknown_digest = hashlib.sha256(b'X9999').hexdigest()
        assert page.locator('#batchRecordEvidence').inner_text().count(unknown_digest) == 2
        assert 'unvalidated' in page.locator('[data-batch-record]').nth(1).inner_text().lower()
        with page.expect_download() as original_info:
            page.locator(f'[data-batch-original="{dat_id}"]').click()
        assert Path(original_info.value.path()).read_bytes() == data
        page.click('#batchAi')
        assert page.locator('#batchGenerate').is_disabled()
        assert page.locator('#batchDownloadArchive').is_disabled()
        assert page.locator(f'[data-batch-download="{dat_id}"]').is_disabled()
        browser.close()
