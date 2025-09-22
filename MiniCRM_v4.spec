block_cipher=None
a=Analysis(['run.py'],pathex=[],binaries=[],datas=[('templates','templates')],hiddenimports=[],hookspath=[],hooksconfig={},runtime_hooks=[],excludes=[],noarchive=False)
pyz=PYZ(a.pure,a.zipped_data,cipher=block_cipher)
exe=EXE(pyz,a.scripts,a.binaries,a.zipfiles,a.datas,name='MiniCRM_v4',debug=False,upx=True,console=True)