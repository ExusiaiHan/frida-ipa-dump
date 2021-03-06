#!/usr/bin/env python3
# encoding: utf-8

import codecs
import sys
import tempfile
import os
import shutil

import frida


def fatal(reason):
    print(reason)
    sys.exit(-1)


def dump(app_name_or_id, device_id, verbose):
    if device_id is None:
        dev = frida.get_usb_device()
    else:
        try:
            dev = next(dev for dev in frida.enumerate_devices()
                       if dev.id.startswith(device_id))
        except StopIteration:
            fatal('device id %s not found' % device_id)

    if dev.type != 'tether':
        fatal('unable to find device')

    try:
        app = next(app for app in dev.enumerate_applications() if
                   app_name_or_id == app.identifier or
                   app_name_or_id == app.name)
    except:
        print('app "%s" not found' % app_name_or_id)
        print('installed app:')
        for app in dev.enumerate_applications():
            print('%s (%s)' % (app.name, app.identifier))
        fatal('')

    task = IPADump(dev, app, verbose=verbose)
    task.run()


class Task(object):

    def __init__(self, session, path, info):
        self.session = session
        self.path = path
        self.info = info
        self.file = open(self.path, 'wb')

    def write(self, data):
        self.file.write(data)

    def finish(self):
        self.close()
        time_pair = tuple(self.info.get(key)
                          for key in ('creation', 'modification'))
        try:
            if all(time_pair):
                os.utime(self.path, time_pair)
            os.chmod(self.path, self.info['permission'])
        except FileNotFoundError:
            pass

    def close(self):
        self.file.close()


class IPADump(object):

    def __init__(self, device, app, verbose=False):
        self.device = device
        self.app = app
        self.session = None
        self.tempdir = None
        self.tasks = {}
        self.script = None
        self.verbose = verbose

    def on_download_start(self, session, relative, info, **kwargs):
        if self.verbose:
            print('downloading', relative)
        local_path = self.local_path(relative)
        self.tasks[session] = Task(session, local_path, info)

    def on_download_data(self, session, data, **kwargs):
        self.tasks[session].write(data)

    def on_download_finish(self, session, **kwargs):
        self.close_session(session)

    def on_download_error(self, session, **kwargs):
        self.close_session(session)

    def close_session(self, session):
        self.tasks[session].finish()
        del self.tasks[session]

    def local_path(self, relative):
        parent = self.tempdir
        local_path = os.path.join(parent, relative)
        if not local_path.startswith(parent):
            raise ValueError('path "%s" is illegal' % relative)
        return local_path

    def on_mkdir(self, path, **kwargs):
        local_path = self.local_path(path)
        os.mkdir(local_path)

    def on_message(self, msg, data):
        if msg.get('type') != 'send':
            print('unknown message:', msg)
            return

        payload = msg.get('payload', {})
        subject = payload.get('subject')
        if subject == 'download':
            method_mapping = {
                'start': self.on_download_start,
                'data': self.on_download_data,
                'end': self.on_download_finish,
                'error': self.on_download_error,
            }
            method = method_mapping[payload.get('event')]
            method(data=data, **payload)
        elif subject == 'finish':
            print('bye')
            self.session.detach()
            sys.exit(0)
        elif subject == 'mkdir':
            self.on_mkdir(**payload)
        else:
            print('unknown message')
            print(msg)

    def inject(self):
        def on_console(level, text):
            print('[%s]' % level, text)

        agent = os.path.join('agent', 'dist.js')
        with codecs.open(agent, 'r', 'utf-8') as fp:
            source = fp.read()
        on_console('info', 'attaching to target')
        pid = self.app.pid or self.device.spawn(self.app.identifier)
        self.session = self.device.attach(pid)
        script = self.session.create_script(source)
        script.set_log_handler(on_console)
        script.on('message', self.on_message)
        script.load()
        script.exports.dump()

    def run(self):
        with tempfile.TemporaryDirectory() as tempdir:
            self.tempdir = tempdir
            self.inject()
            shutil.make_archive(self.app.name, 'zip', tempdir)
            print('write to %s.zip' % self.app.name)
        self.session.detach()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', nargs='?', help='device id (prefix)')
    parser.add_argument('app', help='application name or bundle id')
    parser.add_argument('-v', '--verbose', help='verbose mode')
    args = parser.parse_args()

    dump(args.app, args.device, args.verbose)

if __name__ == '__main__':
    main()
