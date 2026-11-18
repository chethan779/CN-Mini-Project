#!/usr/bin/env python3
"""
insert_screenshots.py
Safely injects the 6 generated screenshots into Reliable_UDP_File_Transfer_SDN_Project_Report.docx:
  - 3 GUI Screenshots (Sender, Receiver, SDN Controller) in Section 8.2
  - 3 Terminal Screenshots (Receiver, Sender, SDN Controller) in Section 8.3
  - GUI Execution instructions in Section 9.6
"""

import os
import shutil
import zipfile
import re
import xml.etree.ElementTree as ET

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCX_PATH = os.path.join(PROJECT_DIR, 'docs', 'Reliable_UDP_File_Transfer_SDN_Project_Report.docx')
BACKUP_PATH = os.path.join(PROJECT_DIR, 'docs', 'Reliable_UDP_File_Transfer_SDN_Project_Report.docx.bak')
OUTPUT_PATH = os.path.join(PROJECT_DIR, 'docs', 'Reliable_UDP_File_Transfer_SDN_Project_Report_updated.docx')
SCREENSHOTS_DIR = os.path.join(PROJECT_DIR, 'screenshots')

IMAGES = [
    {
        'file': '01_sender_transfer_completed.png',
        'media_name': 'image3.png',
        'rel_id': 'rId12',
        'doc_pr_id': '3',
        'cx': 5600000,
        'cy': 3891525,
        'title': 'Figure 3. Sender Client GUI displaying completed transmission (100%), real-time sequence ACK vs. Sent telemetry, delivery breakdown, and SHA-256 integrity verification.',
    },
    {
        'file': '02_receiver_server_completed.png',
        'media_name': 'image4.png',
        'rel_id': 'rId13',
        'doc_pr_id': '4',
        'cx': 5600000,
        'cy': 3891525,
        'title': 'Figure 4. Receiver Server GUI displaying continuous packet reception, reassembly buffer telemetry, cumulative ACK classification, and SHA-256 integrity PASS.',
    },
    {
        'file': '03_sdn_controller_reroute.png',
        'media_name': 'image5.png',
        'rel_id': 'rId14',
        'doc_pr_id': '5',
        'cx': 5600000,
        'cy': 3891525,
        'title': 'Figure 5. SDN Controller GUI visualizer displaying the 4-switch topology (s1–s4), real-time link monitoring, primary path degradation detection, and automatic failover to the backup path.',
    },
    {
        'file': '04_terminal_receiver_server.png',
        'media_name': 'image6.png',
        'rel_id': 'rId15',
        'doc_pr_id': '6',
        'cx': 5800000,
        'cy': 2991142,
        'title': 'Figure 6. Receiver Server terminal console log showing UDP socket binding on port 5005, SYN handshake, packet reception sequence, out-of-order buffering, and SHA-256 digest match.',
    },
    {
        'file': '05_terminal_sender_client.png',
        'media_name': 'image7.png',
        'rel_id': 'rId16',
        'doc_pr_id': '7',
        'cx': 5600000,
        'cy': 4168000,
        'title': 'Figure 7. Sender Client terminal console log showing file chunking, sliding window transmission, ACK reception, timeout recovery, and SDN failure alert dispatch.',
    },
    {
        'file': '06_terminal_sdn_controller.png',
        'media_name': 'image8.png',
        'rel_id': 'rId17',
        'doc_pr_id': '8',
        'cx': 5800000,
        'cy': 2494000,
        'title': 'Figure 8. SDN Controller terminal console log showing UDP alert listener on port 9999, link impairment alert parsing, threshold evaluation, and OpenFlow flow rule installation on Switch s1.',
    },
]

def make_drawing_p(img_info):
    xml_str = f'''<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:pPr><w:jc w:val="center"/></w:pPr>
  <w:r>
    <w:drawing>
      <wp:inline>
        <wp:extent cx="{img_info['cx']}" cy="{img_info['cy']}"/>
        <wp:docPr id="{img_info['doc_pr_id']}" name="Picture {img_info['doc_pr_id']}"/>
        <wp:cNvGraphicFramePr>
          <a:graphicFrameLocks noChangeAspect="1"/>
        </wp:cNvGraphicFramePr>
        <a:graphic>
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic>
              <pic:nvPicPr>
                <pic:cNvPr id="0" name="{img_info['file']}"/>
                <pic:cNvPicPr/>
              </pic:nvPicPr>
              <pic:blipFill>
                <a:blip r:embed="{img_info['rel_id']}"/>
                <a:stretch><a:fillRect/></a:stretch>
              </pic:blipFill>
              <pic:spPr>
                <a:xfrm>
                  <a:off x="0" y="0"/>
                  <a:ext cx="{img_info['cx']}" cy="{img_info['cy']}"/>
                </a:xfrm>
                <a:prstGeom prst="rect"/>
              </pic:spPr>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>'''
    return ET.fromstring(xml_str)

def make_caption_p(caption_text):
    xml_str = f'''<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:pPr><w:jc w:val="center"/></w:pPr>
  <w:r><w:t>{caption_text}</w:t></w:r>
</w:p>'''
    return ET.fromstring(xml_str)

def make_heading2_p(text):
    xml_str = f'''<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:pPr><w:pStyle w:val="Heading2"/></w:pPr>
  <w:r><w:t>{text}</w:t></w:r>
</w:p>'''
    return ET.fromstring(xml_str)

def make_para_p(text):
    xml_str = f'''<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:r><w:t>{text}</w:t></w:r>
</w:p>'''
    return ET.fromstring(xml_str)

def make_code_p(text):
    xml_str = f'''<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:pPr><w:ind w:left="360"/></w:pPr>
  <w:r>
    <w:rPr>
      <w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>
      <w:sz w:val="18"/>
    </w:rPr>
    <w:t>{text}</w:t>
  </w:r>
</w:p>'''
    return ET.fromstring(xml_str)

def main():
    if not os.path.exists(BACKUP_PATH):
        shutil.copy2(DOCX_PATH, BACKUP_PATH)
        print(f"Created backup at {BACKUP_PATH}")

    with zipfile.ZipFile(DOCX_PATH, 'r') as zin:
        doc_xml = zin.read('word/document.xml').decode('utf-8')
        rels_xml = zin.read('word/_rels/document.xml.rels').decode('utf-8')

        # 1. Update rels
        ET.register_namespace('', 'http://schemas.openxmlformats.org/package/2006/relationships')
        rels_tree = ET.fromstring(rels_xml)
        for img in IMAGES:
            new_rel = ET.Element('{http://schemas.openxmlformats.org/package/2006/relationships}Relationship')
            new_rel.attrib['Id'] = img['rel_id']
            new_rel.attrib['Type'] = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'
            new_rel.attrib['Target'] = f"media/{img['media_name']}"
            rels_tree.append(new_rel)
        updated_rels = ET.tostring(rels_tree, encoding='utf-8', xml_declaration=True)

        # 2. Update document.xml
        ns_matches = re.findall(r'xmlns:([a-zA-Z0-9_-]+)=\"([^\"]+)\"', doc_xml)
        for prefix, uri in sorted(set(ns_matches)):
            ET.register_namespace(prefix, uri)

        doc_tree = ET.fromstring(doc_xml)
        w_body = doc_tree.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body')

        # Find target insertion points:
        # Point A: Right after the test cases table (before Section 9)
        # Point B: In Section 9, right after the 3-laptop table (before Section 10)
        idx_sec9 = -1
        idx_sec10 = -1
        for i, child in enumerate(w_body):
            text = ''.join(child.itertext()).strip()
            if text.startswith('9. Running the Project') and idx_sec9 == -1:
                idx_sec9 = i
            elif text.startswith('10. Mapping to the Faculty Evaluation Rubric') and idx_sec10 == -1:
                idx_sec10 = i

        print(f"Found Section 9 at body index {idx_sec9}, Section 10 at body index {idx_sec10}")

        # Build elements for Section 8.2 and 8.3
        sec8_elements = []

        # 8.2 GUI Execution
        sec8_elements.append(make_heading2_p("8.2 Graphical User Interface (GUI) Execution Results and Visual Telemetry"))
        sec8_elements.append(make_para_p(
            "The project includes a native desktop graphical interface built with Tkinter and Matplotlib (app_gui.py and sdn_gui.py). "
            "The GUI visualizes real-time sliding window dynamics, live throughput graphs, packet classification breakdowns, and the SDN switch topology. "
            "The screenshots below demonstrate complete, verified file transfer and dynamic rerouting across all three components."
        ))

        # Figure 3: Sender Client GUI
        sec8_elements.append(make_drawing_p(IMAGES[0]))
        sec8_elements.append(make_caption_p(IMAGES[0]['title']))
        sec8_elements.append(make_para_p(
            "As illustrated in Figure 3, the Sender Client window (app_gui.py) displays full transmission parameters (Server IP 127.0.0.1, Port 5005, SDN IP 127.0.0.1, Port 9999), "
            "file metadata (topo.py, 4.04 KB, 5 chunks, SHA-256 digest), and a 100% progress indicator with status COMPLETED. "
            "The upper graph visualizes real-time packet progress (Sequence Number vs. Elapsed Time), demonstrating steady packet dispatch and cumulative ACK reception. "
            "The lower graph provides a packet delivery and loss breakdown, while the telemetry metrics display 5 packets sent, 5 acknowledged, 0 retransmissions, 0% loss, and live throughput in kbps."
        ))

        # Figure 4: Receiver Server GUI
        sec8_elements.append(make_drawing_p(IMAGES[1]))
        sec8_elements.append(make_caption_p(IMAGES[1]['title']))
        sec8_elements.append(make_para_p(
            "Figure 4 depicts the Receiver Server window (app_gui.py). It shows socket binding on port 5005, dynamic reception of file chunks into the reassembly buffer, "
            "out-of-order handling, and automatic file reassembly. Upon receiving the FIN packet, the server computes the cryptographic SHA-256 checksum of the assembled file on disk "
            "and verifies that it matches the client's original digest. The bar chart classifies packet reception (Data Packets Received vs. Cumulative ACKs Sent vs. Loss/Retransmissions), "
            "providing immediate visual confirmation of protocol correctness."
        ))

        # Figure 5: SDN Controller GUI
        sec8_elements.append(make_drawing_p(IMAGES[2]))
        sec8_elements.append(make_caption_p(IMAGES[2]['title']))
        sec8_elements.append(make_para_p(
            "Figure 5 presents the SDN Controller topology visualizer (sdn_gui.py). The dashboard illustrates the four OpenFlow switches (s1, s2, s3, s4) and endpoints (h1, h3). "
            "During transfer, Switch s1 monitors path health. When the client triggers timeout alerts via UDP port 9999, the controller dynamically switches the active route "
            "from the degraded primary path (s1–s2–s4) to the healthy backup path (s1–s3–s4), installing high-priority OpenFlow rules (priority 200). "
            "The event stream confirms: \"[ALERT] Received client loss alert: 2 timeouts\", followed by \"[REROUTE] Switched traffic to BACKUP path via Switch s3\"."
        ))

        # 8.3 Terminal CLI Execution
        sec8_elements.append(make_heading2_p("8.3 Command-Line Interface (CLI) Execution and Terminal Output"))
        sec8_elements.append(make_para_p(
            "In addition to the graphical interfaces, the underlying protocol engine was validated using command-line execution across the three distributed network roles. "
            "The terminal logs confirm low-level socket operations, protocol handshakes, and OpenFlow flow updates."
        ))

        # Figure 6: Receiver Server Terminal
        sec8_elements.append(make_drawing_p(IMAGES[3]))
        sec8_elements.append(make_caption_p(IMAGES[3]['title']))
        sec8_elements.append(make_para_p(
            "Figure 6 shows the terminal console for server.py. The server binds to 0.0.0.0:5005, receives the SYN packet with file metadata, initializes a new client session, "
            "continuously acknowledges numbered DATA chunks using cumulative ACKs, writes contiguous chunks to the destination file, and verifies the file SHA-256 hash upon completion."
        ))

        # Figure 7: Sender Client Terminal
        sec8_elements.append(make_drawing_p(IMAGES[4]))
        sec8_elements.append(make_caption_p(IMAGES[4]['title']))
        sec8_elements.append(make_para_p(
            "Figure 7 demonstrates the terminal execution of client.py. It details file partitioning into 1 KB chunks, sliding window pipelining (window size 5), round-trip time tracking, "
            "timeout detection, selective retransmission, UDP alert generation to the SDN controller upon repeated loss, and completion reporting with transfer time, throughput, and verified SHA-256 hash."
        ))

        # Figure 8: SDN Controller Terminal
        sec8_elements.append(make_drawing_p(IMAGES[5]))
        sec8_elements.append(make_caption_p(IMAGES[5]['title']))
        sec8_elements.append(make_para_p(
            "Figure 8 illustrates the terminal log for sdn_controller.py. The controller listens on UDP port 9999, parses incoming JSON telemetry alerts from the client, "
            "tracks consecutive packet loss events, and upon exceeding the failure threshold, executes OpenFlow flow-mod commands to route traffic over backup Switch s3."
        ))

        # Build elements for Section 9.6
        sec9_elements = []
        sec9_elements.append(make_heading2_p("9.6 Graphical User Interface (GUI) Execution Guide"))
        sec9_elements.append(make_para_p(
            "To launch the interactive visual interfaces demonstrated in Figures 3–5, open separate terminal windows and run the following commands:"
        ))
        sec9_elements.append(make_code_p("python3 app_gui.py"))
        sec9_elements.append(make_para_p(
            "Launches the unified Sender/Receiver interface. Users can toggle between Sender (Client) and Receiver (Server) roles or launch concurrent windows using the \"New Window\" button. "
            "Quick IP preset buttons allow immediate selection of Localhost (127.0.0.1), LAN IP (auto-detected Wi-Fi address), or Mininet (10.0.0.3)."
        ))
        sec9_elements.append(make_code_p("python3 sdn_gui.py"))
        sec9_elements.append(make_para_p(
            "Launches the SDN Controller Topology Visualizer. It actively displays the status of switches s1 through s4, real-time link states (Healthy vs. Degraded), "
            "and a live event stream documenting dynamic rerouting events triggered by client loss alerts."
        ))

        # Insert into w_body:
        # First insert sec9_elements before Section 10
        # Then insert sec8_elements before Section 9 (note index offset)
        for offset, el in enumerate(sec9_elements):
            w_body.insert(idx_sec10 + offset, el)

        # Re-find Section 9 index because inserting into w_body before idx_sec10 doesn't affect indices before idx_sec10,
        # but let's be 100% rigorous:
        idx_sec9_new = -1
        for i, child in enumerate(w_body):
            text = ''.join(child.itertext()).strip()
            if text.startswith('9. Running the Project'):
                idx_sec9_new = i
                break

        print(f"Inserting Section 8.2 & 8.3 elements before index {idx_sec9_new}")
        for offset, el in enumerate(sec8_elements):
            w_body.insert(idx_sec9_new + offset, el)

        updated_doc = ET.tostring(doc_tree, encoding='utf-8', xml_declaration=True)

        # Write to output docx
        with zipfile.ZipFile(OUTPUT_PATH, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == 'word/document.xml':
                    zout.writestr(item, updated_doc)
                elif item.filename == 'word/_rels/document.xml.rels':
                    zout.writestr(item, updated_rels)
                else:
                    zout.writestr(item, zin.read(item.filename))

            # Add new screenshot images to word/media/
            for img in IMAGES:
                src_path = os.path.join(SCREENSHOTS_DIR, img['file'])
                with open(src_path, 'rb') as f_img:
                    data = f_img.read()
                zout.writestr(f"word/media/{img['media_name']}", data)
                print(f"Embedded {img['file']} as word/media/{img['media_name']} ({len(data)} bytes)")

    # Replace original with updated
    shutil.move(OUTPUT_PATH, DOCX_PATH)
    print(f"\nSuccessfully updated {DOCX_PATH}!")

if __name__ == '__main__':
    main()
