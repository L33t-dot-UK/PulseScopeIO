import json, threading, time, unittest, urllib.request, urllib.error, io, wave
from pathlib import Path
import server
from pypdf import PdfReader


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:" + str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()

    def request(self, path, body=None, token=True):
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "X-Local-Token": server.TOKEN if token else "bad",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            req, timeout=15
        ) as r:
            return r.read()

    def test_csv_channels_import_reanalysis_and_export(self):
        from tests.test_csv_channels import CsvChannelTests
        fixture=CsvChannelTests();fixture.setUp()
        try:
            a,_=fixture.channel('A');b,_=fixture.channel('B')
            d=json.loads(self.request('/api/open-csv-channels',{'channels':{'A':str(a),'B':str(b)},'options':{'slew_mode':'none'}}))
            for step in range(2):
                for _ in range(200):
                    status=json.loads(self.request('/api/status?id='+d['id']))
                    if status['ready'] or status['error']:break
                    time.sleep(.02)
                self.assertIsNone(status['error']);self.assertTrue(status['ready'])
                self.assertEqual(set(status['options']['csv_channel_paths']),{'A','B'})
                if step==0:
                    d=json.loads(self.request('/api/reanalyse',{'id':d['id'],'options':{'slew_mode':'none','delimiter':','}}))
            payload=self.request('/api/export?id='+d['id']+'&kind=csv')
            self.assertTrue(payload.startswith(b'Time_s,A,-A,B,-B,Z,-Z'))
        finally:fixture.tearDown()

    def test_phase_box_and_filtered_error_navigation(self):
        import numpy as np
        from tests.test_csv_channels import CsvChannelTests
        fixture=CsvChannelTests();fixture.setUp()
        try:
            a,_=fixture.channel('A',count=10000)
            b,_=fixture.channel('B',count=10000)
            sample=np.arange(10000);phase=sample%100
            voltage=np.where((phase>=25)&(phase<75),5.,0.)
            voltage[4000:4100]=np.where((phase[4000:4100]>=40)&(phase[4000:4100]<90),5.,0.)
            np.savetxt(b,np.column_stack([sample*.0001,voltage]),delimiter=',',header='Time_s,Voltage',comments='')
            job=json.loads(self.request('/api/open-csv-channels',{'channels':{'A':str(a),'B':str(b)},'options':{'slew_mode':'none'}}))['id']
            for _ in range(200):
                status=json.loads(self.request('/api/status?id='+job))
                if status['ready'] or status['error']:break
                time.sleep(.02)
            self.assertIsNone(status['error'])
            self.assertEqual(status['phase_analysis']['outliers'],1)
            event=json.loads(self.request('/api/next-fault?id='+job+'&kind=A%2FB%20phase%20outlier'))['event']
            self.assertEqual(event['kind'],'A/B phase outlier')
            hidden=json.loads(self.request('/api/next-fault?id='+job+'&kind=A%2FB%20phase%20outlier&exclude_kind=A%2FB%20phase%20outlier'))
            self.assertIsNone(hidden['event'])
            self.assertEqual(event['channels'],['A','B'])
            self.assertEqual(event['zoom_cycles'],16)
        finally:fixture.tearDown()

    def test_unlabelled_csv_health_summary_and_asset(self):
        import numpy as np
        from tests.test_csv_channels import CsvChannelTests
        fixture=CsvChannelTests();fixture.setUp()
        try:
            path=fixture.folder/'unlabelled.csv'
            i=np.arange(4000);a=np.where(i%100<50,5.,0.);b=np.where((i-25)%100<50,5.,0.);z=np.where(i%1000<5,5.,0.)
            np.savetxt(path,np.column_stack([i*.0001,a,5-a,b,5-b,z,5-z]),delimiter=',',header='unknown,ch1,ch2,ch3,ch4,ch5,ch6',comments='')
            job=json.loads(self.request('/api/open',{'path':str(path),'options':{'slew_mode':'none'}}))['id']
            for _ in range(200):
                status=json.loads(self.request('/api/status?id='+job))
                if status['ready'] or status['error']:break
                time.sleep(.02)
            self.assertIsNone(status['error']);self.assertTrue(status['ready'])
            self.assertEqual(status['present'],list(range(6)))
            self.assertTrue(status['csv_columns']['assumed'])
            self.assertEqual(status['csv_columns']['assignments']['-Z'],7)
            self.assertEqual(len(status['advanced_analysis']['differential']),3)
            self.assertEqual(status['advanced_analysis']['quadrature']['simultaneous'],0)
            self.assertTrue(status['advanced_analysis']['speed']['available'])
            self.assertIn(b'function renderHealth',self.request('/health.js'))
        finally:fixture.tearDown()

    def test_renamed_csv_prepared_download(self):
        from tests.test_csv_layout import CsvLayoutTests
        fixture=CsvLayoutTests();fixture.setUp()
        try:
            r,_=fixture.load('unknown,col1,col2',channels=2)
            server.JOBS[r.id]=r
            status=json.loads(self.request('/api/status?id='+r.id))
            self.assertEqual(status['csv_export_columns']['headers'],['unknown','col1','col2'])
            original=r.source.read_bytes()
            d=json.loads(self.request('/api/prepare-export',{'id':r.id,'kind':'csv','csv_headers':['Time_s','A','B']}))
            for _ in range(200):
                state=json.loads(self.request('/api/export-status?id='+r.id+'&export='+d['export']))
                if state['ready'] or state.get('error'):break
                time.sleep(.02)
            self.assertTrue(state['ready'],state)
            payload=self.request('/api/export-file?id='+r.id+'&export='+d['export'])
            self.assertTrue(payload.startswith(b'Time_s,A,B\n'))
            self.assertEqual(payload.split(b'\n',1)[1],original.split(b'\n',1)[1])
            self.assertEqual(r.source.read_bytes(),original)
            self.assertIn(b'function renderCsvColumns',self.request('/columns.js'))
        finally:
            server.JOBS.pop(r.id,None)
            fixture.tearDown()

    def test_bearing_error_generation_profile(self):
        result=json.loads(self.request('/api/generate',{'kind':'bearing','seed':42,'options':{'slew_mode':'none'}}))
        profile=result['generated']['bearing_error']
        self.assertGreaterEqual(profile['error_degrees'],1);self.assertLessEqual(profile['error_degrees'],5)
        for _ in range(400):
            status=json.loads(self.request('/api/status?id='+result['id']))
            if status['ready'] or status['error']:break
            time.sleep(.02)
        self.assertTrue(status['ready'],status.get('error'))
        self.assertEqual(status['options']['bearing_test'],profile)
        self.assertTrue(status['options']['reference']);self.assertEqual(status['replay_ppr'],8192)
        self.assertEqual(status['pulse_counts']['missing_by_channel']['A'],profile['missing_cycles'])
        self.assertEqual(status['pulse_counts']['missing_by_channel']['B'],profile['missing_cycles'])

    def test_binary_import_reanalysis_and_export(self):
        from tests.test_binary_import import BinaryTests
        fixture = BinaryTests()
        fixture.setUp()
        try:
            path = fixture.channel()
            d = json.loads(self.request('/api/open-bin', {'channels': {'A':str(path)},'options':{'slew_mode':'none'}}))
            for _ in range(200):
                status = json.loads(self.request('/api/status?id='+d['id']))
                if status['ready'] or status['error']:break
                time.sleep(.02)
            self.assertIsNone(status['error']);self.assertTrue(status['ready'])
            d = json.loads(self.request('/api/reanalyse', {'id':d['id'],'options':{'slew_mode':'none','window':3,'delimiter':','}}))
            for _ in range(200):
                status = json.loads(self.request('/api/status?id='+d['id']))
                if status['ready'] or status['error']:break
                time.sleep(.02)
            self.assertIsNone(status['error']);self.assertTrue(status['ready'])
            self.assertEqual(status['options']['binary_paths']['A'],str(path))
            payload=self.request('/api/export?id='+d['id']+'&kind=csv')
            self.assertTrue(payload.startswith(b'Time_s,A,-A,B,-B,Z,-Z'))
        finally:
            fixture.tearDown()

    def test_bin_browser_uses_bin_file_filter(self):
        from unittest.mock import patch
        with patch('file_picker.choose_file', return_value='capture.BIN') as choose:
            result=json.loads(self.request('/api/browse',{'kind':'bin'}))
            self.assertEqual(result['path'],'capture.BIN')
            choose.assert_called_once_with('bin')
        with patch('file_picker.choose_file', return_value='capture.csv') as choose:
            result=json.loads(self.request('/api/browse',{'kind':'csv-channel'}))
            self.assertEqual(result['path'],'capture.csv')
            choose.assert_called_once_with('csv-channel')

    def test_end_to_end(self):
        d = json.loads(
            self.request(
                "/api/open",
                {
                    "path": str(Path("examples/clean.csv").resolve()),
                    "options": {"low": 0, "high": 0},
                },
            )
        )
        job = d["id"]
        for _ in range(200):
            status = json.loads(self.request("/api/status?id=" + job))
            if status["error"]:
                self.fail(status["error"])
            if status["ready"]:
                break
            time.sleep(0.025)
        self.assertTrue(status["ready"])
        self.assertEqual(status["ppr"], 32)
        speed = json.loads(self.request("/api/rpm", {"id": job, "rpm": "90"}))
        self.assertEqual(speed["rpm"], 90)
        self.assertEqual(speed["rpm_source"], "entered")
        self.assertTrue(json.loads(self.request("/api/plot?id=" + job))["points"])
        csv = self.request("/api/export?id=" + job + "&kind=csv").decode()
        self.assertEqual(len(csv.splitlines()), 2049)
        wav = self.request("/api/export?id=" + job + "&kind=wav")
        with wave.open(io.BytesIO(wav)) as w:
            self.assertEqual(w.getnchannels(), 6)
            self.assertEqual(w.getnframes(), 2048)
        pdf = self.request("/api/export?id=" + job + "&kind=pdf&serial=TEST123")
        (server.CACHE / "qa-report.pdf").write_bytes(pdf)
        self.assertIn("TEST123", PdfReader(io.BytesIO(pdf)).pages[0].extract_text())
        reader = PdfReader(io.BytesIO(pdf))
        self.assertEqual(len(reader.pages), 2)
        report_text = "\n".join(page.extract_text() for page in reader.pages)
        for heading in [
            "Summary and diagnostics",
            "A/B phase shift",
            "Missed-pulse check",
            "RS422 Rx Output",
            "Channel eye diagrams",
            "Channel period-jitter histograms",
            "Per-channel measurements",
        ]:
            self.assertIn(heading, report_text)
        self.assertIn(b"Pulse Scope IO", self.request("/"))
        re = json.loads(
            self.request(
                "/api/reanalyse",
                {"id": job, "options": {"low": 0, "high": 0, "window": 3}},
            )
        )
        self.assertNotEqual(re["id"], job)

    def wait_ready(self, job):
        for _ in range(600):
            state = json.loads(self.request("/api/status?id=" + job))
            if state["error"]:
                self.fail(state["error"])
            if state["ready"]:
                return state
            time.sleep(0.025)
        self.fail("Analysis did not complete")

    def test_generated_data_and_span_api(self):
        for kind in ("clean", "missing", "jitter"):
            with self.subTest(kind=kind):
                d = json.loads(
                    self.request(
                        "/api/generate",
                        {
                            "kind": kind,
                            "seed": 123,
                            "differential": True,
                            "options": {"slew_mode": "online"},
                        },
                    )
                )
                self.assertEqual(d["generated"]["seed"], 123)
                state = self.wait_ready(d["id"])
                self.assertEqual(state["count"], d["generated"]["samples"])
                self.assertEqual(state["options"]["reference"], kind == "missing")
                if kind == "clean":
                    self.assertEqual(state["diagnostics"]["oos_cycles"], 0)
                if kind == "missing":
                    self.assertGreater(state["error_count"], 0)
                    counts = json.loads(self.request("/api/pulse-counts?id=" + d["id"]))
                    self.assertGreater(counts["missing_total"], 0)
                    self.assertEqual(
                        sum(row["total"] for row in counts["rows"]),
                        counts["missing_total"],
                    )
                span = json.loads(
                    self.request(
                        "/api/measurements?id=" + d["id"] + "&start=0.1&end=0.7"
                    )
                )
                self.assertLess(span["count"], state["count"])
                self.assertEqual(len(span["metrics"]), 9)

    def test_original_csv_and_bipolar_wav(self):
        d = json.loads(
            self.request(
                "/api/open",
                {
                    "path": str(Path("audit/fixtures/clean.csv").resolve()),
                    "options": {"low": 2.5, "high": 2.5, "rpm": 90},
                },
            )
        )
        job = d["id"]
        self.wait_ready(job)
        data = self.request("/api/export?id=" + job + "&kind=csv")
        self.assertEqual(data, Path("audit/fixtures/clean.csv").read_bytes())
        wav = self.request("/api/export?id=" + job + "&kind=wav")
        import numpy as np

        with wave.open(io.BytesIO(wav)) as w:
            self.assertEqual(w.getframerate(), 7680)
            samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            self.assertEqual(set(samples), {-32768, 32767})
        analog = self.request(
            "/api/export?id=" + job + "&kind=wav&wav_mode=analog&scale=5&rate=48000"
        )
        with wave.open(io.BytesIO(analog)) as w:
            self.assertEqual(w.getframerate(), 48000)

    def test_prepared_export_lifecycle(self):
        d = json.loads(
            self.request(
                "/api/open",
                {
                    "path": str(Path("audit/fixtures/clean.csv").resolve()),
                    "options": {"low": 2.5, "high": 2.5},
                },
            )
        )
        job = d["id"]
        self.wait_ready(job)
        prepared = json.loads(
            self.request("/api/prepare-export", {"id": job, "kind": "csv"})
        )
        key = prepared["export"]
        for _ in range(300):
            state = json.loads(
                self.request("/api/export-status?id=" + job + "&export=" + key)
            )
            self.assertIsNone(state["error"])
            if state["ready"]:
                break
            time.sleep(0.01)
        self.assertTrue(state["ready"])
        content = self.request("/api/export-file?id=" + job + "&export=" + key)
        self.assertEqual(content, Path("audit/fixtures/clean.csv").read_bytes())
        self.assertNotIn(key, server.EXPORTS)
        failed = json.loads(
            self.request("/api/prepare-export", {"id": job, "kind": "invalid"})
        )["export"]
        for _ in range(100):
            state = json.loads(
                self.request("/api/export-status?id=" + job + "&export=" + failed)
            )
            if state["error"]:
                break
            time.sleep(0.01)
        self.assertIn("Unknown export", state["error"])

    def test_token_required(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request("/api/open", {"path": "anything"}, False)
        self.assertEqual(e.exception.code, 403)

    def test_merge_endpoint_and_reimport(self):
        import numpy as np

        folder = server.CACHE / "http-merge-test"
        folder.mkdir(exist_ok=True)
        x = np.arange(5000)
        a = (x % 100 < 50).astype(float) * 5
        b = ((x - 25) % 100 < 50).astype(float) * 5
        z = ((x - 100) % 1000 < 20).astype(float) * 5
        full = np.column_stack((x / 10000, a, 5 - a, b, 5 - b, z, 5 - z))
        paths = []
        for role, cols, skip in (("A", [0, 1, 2, 5, 6], 0), ("B", [0, 3, 4, 5, 6], 37)):
            path = folder / (role + ".csv")
            np.savetxt(
                path,
                full[skip:, cols],
                delimiter=",",
                header="Time_s,CH1,CH2,CH3,CH4",
                comments="",
            )
            paths.append(str(path.resolve()))
        reference = folder / "reference.csv"
        np.savetxt(
            reference,
            full[::2, :5],
            delimiter=",",
            header="Time_s,A,-A,B,-B",
            comments="",
        )
        d = json.loads(
            self.request(
                "/api/merge",
                {
                    "path_a": paths[0],
                    "path_b": paths[1],
                    "path_c": str(reference.resolve()),
                    "options": {},
                },
            )
        )
        state = self.wait_ready(d["id"])
        self.assertEqual(state["present"], list(range(6)))
        self.assertEqual(state["merge_info"]["offset_samples"], -37)
        self.assertEqual(state["merge_info"]["alignment_check"]["status"], "match")
        # Changing an import option must re-import BOTH captures.
        d = json.loads(
            self.request(
                "/api/reanalyse", {"id": d["id"], "options": {"delimiter": ","}}
            )
        )
        state = self.wait_ready(d["id"])
        self.assertEqual(state["merge_info"]["retained_samples"], 4963)
        self.assertEqual(state["merge_info"]["alignment_check"]["status"], "match")
        content = self.request("/api/export?id=" + d["id"] + "&kind=csv").decode()
        self.assertEqual(content.splitlines()[0], "Time_s,A,-A,B,-B,Z,-Z")
        values = np.loadtxt(io.StringIO(content), delimiter=",", skiprows=1)
        np.testing.assert_array_equal(values[:, 1:], full[37:, 1:])

    def test_alignment_mode_choice_reimports_and_labels_refined_export(self):
        options = {"alignment_mode": "check", "slew_mode": "none"}
        opened = json.loads(
            self.request(
                "/api/merge",
                {
                    "path_a": str(Path("examples/merge-A.csv").resolve()),
                    "path_b": str(Path("examples/merge-B.csv").resolve()),
                    "path_c": str(Path("examples/merge-alignment.csv").resolve()),
                    "options": options,
                },
            )
        )
        checked = self.wait_ready(opened["id"])
        self.assertFalse(
            checked["merge_info"].get("refinement", {}).get("applied", False)
        )
        opened = json.loads(
            self.request(
                "/api/reanalyse",
                {
                    "id": checked["id"],
                    "options": dict(options, alignment_mode="refine"),
                },
            )
        )
        refined = self.wait_ready(opened["id"])
        self.assertTrue(refined["merge_info"]["refinement"]["applied"])
        self.assertFalse(
            refined["merge_info"]["alignment_check"]["comparison_independent"]
        )
        request = urllib.request.Request(
            self.base + "/api/export?id=" + refined["id"] + "&kind=csv"
        )
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=15
        ) as response:
            self.assertIn("refined-B.csv", response.headers["Content-Disposition"])
            self.assertTrue(response.read().startswith(b"Time_s,A,-A,B,-B,Z,-Z\n"))
        updated = json.loads(
            self.request(
                "/api/reanalyse",
                {
                    "id": refined["id"],
                    "options": dict(options, alignment_mode="refine", window=3),
                },
            )
        )
        adjusted = self.wait_ready(updated["id"])
        self.assertTrue(adjusted["merge_info"]["refinement"]["applied"])
        self.assertFalse(
            adjusted["merge_info"]["alignment_check"]["comparison_independent"]
        )
        opened = json.loads(
            self.request("/api/reanalyse", {"id": adjusted["id"], "options": options})
        )
        restored = self.wait_ready(opened["id"])
        self.assertFalse(
            restored["merge_info"].get("refinement", {}).get("applied", False)
        )
        self.assertEqual(restored["count"], checked["count"])
        self.assertEqual(
            restored["merge_info"]["retained_samples"],
            checked["merge_info"]["retained_samples"],
        )


if __name__ == "__main__":
    unittest.main()
