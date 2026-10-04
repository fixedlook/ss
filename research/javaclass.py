"""Minimal Java .class parser - constant pool + members + bytecode disassembly.

No JVM needed. Written to inspect the user's BidAskExporterStudy.class (only a
compiled jar was provided) and the MotiveWave SDK.
"""
import struct
import sys
import zipfile

TAGS = {1: 'Utf8', 3: 'Int', 4: 'Float', 5: 'Long', 6: 'Double', 7: 'Class',
        8: 'String', 9: 'Fieldref', 10: 'Methodref', 11: 'IfaceMethodref',
        12: 'NameAndType', 15: 'MethodHandle', 16: 'MethodType', 17: 'Dynamic',
        18: 'InvokeDynamic', 19: 'Module', 20: 'Package'}


class R:
    def __init__(self, b):
        self.b, self.i = b, 0

    def u1(self):
        v = self.b[self.i]; self.i += 1; return v

    def u2(self):
        v = struct.unpack_from('>H', self.b, self.i)[0]; self.i += 2; return v

    def u4(self):
        v = struct.unpack_from('>I', self.b, self.i)[0]; self.i += 4; return v

    def take(self, n):
        v = self.b[self.i:self.i + n]; self.i += n; return v


class ClassFile:
    def __init__(self, data):
        r = R(data)
        assert r.u4() == 0xCAFEBABE, 'not a class file'
        self.minor, self.major = r.u2(), r.u2()
        n = r.u2()
        self.cp = [None] * n
        i = 1
        while i < n:
            tag = r.u1()
            if tag == 1:
                ln = r.u2(); self.cp[i] = ('Utf8', r.take(ln).decode('utf-8', 'replace'))
            elif tag in (3, 4):
                self.cp[i] = (TAGS[tag], r.u4())
            elif tag in (5, 6):
                self.cp[i] = (TAGS[tag], struct.unpack('>q' if tag == 5 else '>d', r.take(8))[0])
                i += 1
            elif tag in (7, 8, 16, 19, 20):
                self.cp[i] = (TAGS[tag], r.u2())
            elif tag in (9, 10, 11, 12, 17, 18):
                self.cp[i] = (TAGS[tag], r.u2(), r.u2())
            elif tag == 15:
                self.cp[i] = (TAGS[tag], r.u1(), r.u2())
            else:
                raise ValueError(f'bad tag {tag} at {i}')
            i += 1
        self.access, self.this, self.super = r.u2(), r.u2(), r.u2()
        self.interfaces = [r.u2() for _ in range(r.u2())]
        self.fields = self._members(r)
        self.methods = self._members(r)
        self.attrs = self._attrs(r)

    def _attrs(self, r):
        out = []
        for _ in range(r.u2()):
            ni, ln = r.u2(), r.u4()
            out.append((self.utf(ni), r.take(ln)))
        return out

    def _members(self, r):
        out = []
        for _ in range(r.u2()):
            acc, ni, di = r.u2(), r.u2(), r.u2()
            out.append({'acc': acc, 'name': self.utf(ni), 'desc': self.utf(di),
                        'attrs': self._attrs(r)})
        return out

    def utf(self, i):
        e = self.cp[i]
        return e[1] if e and e[0] == 'Utf8' else None

    def cls(self, i):
        e = self.cp[i]
        return self.utf(e[1]) if e and e[0] == 'Class' else None

    def nat(self, i):
        e = self.cp[i]
        return (self.utf(e[1]), self.utf(e[2])) if e and e[0] == 'NameAndType' else (None, None)

    def ref(self, i):
        e = self.cp[i]
        if not e:
            return None
        if e[0] in ('Methodref', 'Fieldref', 'IfaceMethodref'):
            return (self.cls(e[1]), *self.nat(e[2]))
        return None


# ---- opcode table (mnemonic, operand kinds) ----
OPS = {}
for op, nm in [(0x00, 'nop'), (0x01, 'aconst_null'), (0x02, 'iconst_m1'), (0x03, 'iconst_0'),
               (0x04, 'iconst_1'), (0x05, 'iconst_2'), (0x06, 'iconst_3'), (0x07, 'iconst_4'),
               (0x08, 'iconst_5'), (0x09, 'lconst_0'), (0x0a, 'lconst_1'), (0x0b, 'fconst_0'),
               (0x0c, 'fconst_1'), (0x0d, 'fconst_2'), (0x0e, 'dconst_0'), (0x0f, 'dconst_1'),
               (0x10, 'bipush'), (0x11, 'sipush'), (0x12, 'ldc'), (0x13, 'ldc_w'),
               (0x14, 'ldc2_w'), (0x15, 'iload'), (0x16, 'lload'), (0x17, 'fload'),
               (0x18, 'dload'), (0x19, 'aload'), (0x1a, 'iload_0'), (0x1b, 'iload_1'),
               (0x1c, 'iload_2'), (0x1d, 'iload_3'), (0x1e, 'lload_0'), (0x1f, 'lload_1'),
               (0x20, 'lload_2'), (0x21, 'lload_3'), (0x22, 'fload_0'), (0x23, 'fload_1'),
               (0x24, 'fload_2'), (0x25, 'fload_3'), (0x26, 'dload_0'), (0x27, 'dload_1'),
               (0x28, 'dload_2'), (0x29, 'dload_3'), (0x2a, 'aload_0'), (0x2b, 'aload_1'),
               (0x2c, 'aload_2'), (0x2d, 'aload_3'), (0x2e, 'iaload'), (0x2f, 'laload'),
               (0x30, 'faload'), (0x31, 'daload'), (0x32, 'aaload'), (0x33, 'baload'),
               (0x34, 'caload'), (0x35, 'saload'), (0x36, 'istore'), (0x37, 'lstore'),
               (0x38, 'fstore'), (0x39, 'dstore'), (0x3a, 'astore'), (0x3b, 'istore_0'),
               (0x3c, 'istore_1'), (0x3d, 'istore_2'), (0x3e, 'istore_3'), (0x3f, 'lstore_0'),
               (0x40, 'lstore_1'), (0x41, 'lstore_2'), (0x42, 'lstore_3'), (0x43, 'fstore_0'),
               (0x44, 'fstore_1'), (0x45, 'fstore_2'), (0x46, 'fstore_3'), (0x47, 'dstore_0'),
               (0x48, 'dstore_1'), (0x49, 'dstore_2'), (0x4a, 'dstore_3'), (0x4b, 'astore_0'),
               (0x4c, 'astore_1'), (0x4d, 'astore_2'), (0x4e, 'astore_3'), (0x4f, 'iastore'),
               (0x50, 'lastore'), (0x51, 'fastore'), (0x52, 'dastore'), (0x53, 'aastore'),
               (0x54, 'bastore'), (0x55, 'castore'), (0x56, 'sastore'), (0x57, 'pop'),
               (0x58, 'pop2'), (0x59, 'dup'), (0x5a, 'dup_x1'), (0x5b, 'dup_x2'),
               (0x5c, 'dup2'), (0x5d, 'dup2_x1'), (0x5e, 'dup2_x2'), (0x5f, 'swap'),
               (0x60, 'iadd'), (0x61, 'ladd'), (0x62, 'fadd'), (0x63, 'dadd'), (0x64, 'isub'),
               (0x65, 'lsub'), (0x66, 'fsub'), (0x67, 'dsub'), (0x68, 'imul'), (0x69, 'lmul'),
               (0x6a, 'fmul'), (0x6b, 'dmul'), (0x6c, 'idiv'), (0x6d, 'ldiv'), (0x6e, 'fdiv'),
               (0x6f, 'ddiv'), (0x70, 'irem'), (0x71, 'lrem'), (0x72, 'frem'), (0x73, 'drem'),
               (0x74, 'ineg'), (0x75, 'lneg'), (0x76, 'fneg'), (0x77, 'dneg'), (0x78, 'ishl'),
               (0x79, 'lshl'), (0x7a, 'ishr'), (0x7b, 'lshr'), (0x7c, 'iushr'), (0x7d, 'lushr'),
               (0x7e, 'iand'), (0x7f, 'land'), (0x80, 'ior'), (0x81, 'lor'), (0x82, 'ixor'),
               (0x83, 'lxor'), (0x84, 'iinc'), (0x85, 'i2l'), (0x86, 'i2f'), (0x87, 'i2d'),
               (0x88, 'l2i'), (0x89, 'l2f'), (0x8a, 'l2d'), (0x8b, 'f2i'), (0x8c, 'f2l'),
               (0x8d, 'f2d'), (0x8e, 'd2i'), (0x8f, 'd2l'), (0x90, 'd2f'), (0x91, 'i2b'),
               (0x92, 'i2c'), (0x93, 'i2s'), (0x94, 'lcmp'), (0x95, 'fcmpl'), (0x96, 'fcmpg'),
               (0x97, 'dcmpl'), (0x98, 'dcmpg'), (0x99, 'ifeq'), (0x9a, 'ifne'), (0x9b, 'iflt'),
               (0x9c, 'ifge'), (0x9d, 'ifgt'), (0x9e, 'ifle'), (0x9f, 'if_icmpeq'),
               (0xa0, 'if_icmpne'), (0xa1, 'if_icmplt'), (0xa2, 'if_icmpge'), (0xa3, 'if_icmpgt'),
               (0xa4, 'if_icmple'), (0xa5, 'if_acmpeq'), (0xa6, 'if_acmpne'), (0xa7, 'goto'),
               (0xa8, 'jsr'), (0xa9, 'ret'), (0xaa, 'tableswitch'), (0xab, 'lookupswitch'),
               (0xac, 'ireturn'), (0xad, 'lreturn'), (0xae, 'freturn'), (0xaf, 'dreturn'),
               (0xb0, 'areturn'), (0xb1, 'return'), (0xb2, 'getstatic'), (0xb3, 'putstatic'),
               (0xb4, 'getfield'), (0xb5, 'putfield'), (0xb6, 'invokevirtual'),
               (0xb7, 'invokespecial'), (0xb8, 'invokestatic'), (0xb9, 'invokeinterface'),
               (0xba, 'invokedynamic'), (0xbb, 'new'), (0xbc, 'newarray'), (0xbd, 'anewarray'),
               (0xbe, 'arraylength'), (0xbf, 'athrow'), (0xc0, 'checkcast'), (0xc1, 'instanceof'),
               (0xc2, 'monitorenter'), (0xc3, 'monitorexit'), (0xc6, 'ifnull'), (0xc7, 'ifnonnull'),
               (0xc8, 'goto_w'), (0xc9, 'jsr_w')]:
    OPS[op] = nm

U1 = {'bipush', 'ldc', 'iload', 'lload', 'fload', 'dload', 'aload', 'istore', 'lstore',
      'fstore', 'dstore', 'astore', 'ret', 'newarray'}
U2 = {'sipush', 'ldc_w', 'ldc2_w', 'getstatic', 'putstatic', 'getfield', 'putfield',
      'invokevirtual', 'invokespecial', 'invokestatic', 'new', 'anewarray', 'checkcast',
      'instanceof', 'ifnull', 'ifnonnull'}
BR2 = {'ifeq', 'ifne', 'iflt', 'ifge', 'ifgt', 'ifle', 'if_icmpeq', 'if_icmpne', 'if_icmplt',
       'if_icmpge', 'if_icmpgt', 'if_icmple', 'if_acmpeq', 'if_acmpne', 'goto', 'jsr'}


def disasm(cf, code):
    out, i = [], 0
    while i < len(code):
        pc = i
        op = code[i]; i += 1
        nm = OPS.get(op, f'op_{op:02x}')
        arg = ''
        if nm == 'tableswitch':
            while i % 4:
                i += 1
            d, lo, hi = struct.unpack_from('>iii', code, i); i += 12
            for _ in range(hi - lo + 1):
                i += 4
            arg = f'[{lo}..{hi}] default->{pc+d}'
        elif nm == 'lookupswitch':
            while i % 4:
                i += 1
            d, np_ = struct.unpack_from('>ii', code, i); i += 8 + 8 * np_
            arg = f'n={np_} default->{pc+d}'
        elif nm in BR2:
            off = struct.unpack_from('>h', code, i)[0]; i += 2
            arg = f'->{pc+off}'
        elif nm == 'wide':
            op2 = code[i]; i += 1
            i += 4 if op2 == 0x84 else 2
            arg = f'wide {OPS.get(op2, hex(op2))}'
        elif nm in U1:
            idx = code[i]; i += 1
            arg = f'#{idx}'
        elif nm in U2:
            idx = struct.unpack_from('>H', code, i)[0]; i += 2
            if nm in ('ldc', 'ldc_w', 'ldc2_w'):
                e = cf.cp[idx]
                arg = f'#{idx} {e[0]}:{e[1]!r}' if e and e[0] == 'Utf8' else \
                      f'#{idx} {e[0]}:{cf.utf(e[1]) if e and e[0] in ("Class","String") else e[1] if e else "?"}'
            elif nm.startswith('invoke') or nm in ('getstatic', 'putstatic', 'getfield', 'putfield'):
                r = cf.ref(idx)
                arg = f'#{idx} {r[0]}.{r[1]}{r[2]}' if r else f'#{idx}'
            elif nm in ('new', 'checkcast', 'instanceof', 'anewarray'):
                arg = f'#{idx} {cf.cls(idx)}'
            else:
                arg = f'#{idx}'
        elif nm == 'invokeinterface':
            idx = struct.unpack_from('>H', code, i)[0]; i += 4
            r = cf.ref(idx)
            arg = f'#{idx} {r[0]}.{r[1]}{r[2]}' if r else f'#{idx}'
        elif nm == 'invokedynamic':
            idx = struct.unpack_from('>H', code, i)[0]; i += 3
            arg = f'#{idx}'
        elif nm == 'iinc':
            a, b = struct.unpack_from('>bb', code, i); i += 2
            arg = f'{a} {b}'
        elif nm in ('goto_w', 'jsr_w'):
            off = struct.unpack_from('>i', code, i)[0]; i += 4
            arg = f'->{pc+off}'
        out.append(f'  {pc:>4}: {nm:<16} {arg}')
    return out


def load(path, member=None):
    if path.endswith('.jar') or path.endswith('.zip'):
        z = zipfile.ZipFile(path)
        if member:
            return ClassFile(z.read(member)), z
        return None, z
    return ClassFile(open(path, 'rb').read()), None


if __name__ == '__main__':
    path, member = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else None)
    cf, z = load(path, member)
    print(f'class {cf.cls(cf.this)}  extends {cf.cls(cf.super)}  '
          f'[major {cf.major}]')
    print(f'interfaces: {[cf.cls(i) for i in cf.interfaces]}')
    print('\nfields:')
    for f in cf.fields:
        print(f'  {f["desc"]:<40} {f["name"]}')
    print('\nmethods:')
    for m in cf.methods:
        print(f'  {m["desc"]:<60} {m["name"]}')
    print('\nstring constants:')
    for e in cf.cp:
        if e and e[0] == 'String':
            print(f'  {cf.utf(e[1])!r}')
    print('\nexternal references:')
    seen = set()
    for e in cf.cp:
        if e and e[0] in ('Methodref', 'Fieldref', 'IfaceMethodref'):
            c, n, d = cf.ref(cf.cp.index(e))
            if (c, n) not in seen:
                seen.add((c, n))
                print(f'  {c}.{n}{d}')
