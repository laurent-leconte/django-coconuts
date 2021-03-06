# -*- coding: utf-8 -*-
#
# Copyright (C) 2008-2012 Jeremy Lainé
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import os
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils.http import http_date, urlquote
from django.utils.translation import ugettext as _
from django.views.generic.simple import redirect_to
import django.views.static

from coconuts import notifications
from coconuts.forms import AddFileForm, AddFolderForm, PhotoForm, ShareForm, ShareAccessFormSet
from coconuts.models import File, Folder, Photo, NamedAcl, PERMISSIONS

PHOTO_SIZE = 1024
THUMB_SIZE = 128

def FolderContext(request, folder, args):
    args.update({
        'folder': folder,
        'can_manage': folder.has_perm('can_manage', request.user),
        'can_write': folder.has_perm('can_write', request.user)})
    return RequestContext(request, args)

def forbidden(request):
    """Return an error page when the user attempts something forbidden."""
    resp = render_to_response('coconuts/forbidden.html', RequestContext(request))
    resp.status_code = 403
    return resp

def make_nav(item, collection):
    idx = collection.index(item)
    nav = { 'number': idx +1, 'total': len(collection) }
    if idx > 0:
        nav['previous'] = collection[idx-1]
    else:
        nav['previous'] = None
    if idx < len(collection) - 1:
        nav['next'] = collection[idx+1]
    else:
        nav['next'] = None
    return nav

def add_file(request, path):
    try:
        folder = Folder(path)
    except Folder.DoesNotExist:
        raise Http404

    # check permissions
    if not folder.has_perm('can_write', request.user):
        return forbidden(request)

    if request.method == 'POST':
        form = AddFileForm(request.POST, request.FILES)
        if form.is_valid():
            filename = request.FILES['upload'].name
            filepath = os.path.join(folder.filepath(), request.FILES['upload'].name)
            if os.path.exists(filepath):
                messages.warning(request, "File '%s' already exists." % filename)
            else:
                try:
                    fp = file(filepath, 'wb')
                    for chunk in request.FILES['upload'].chunks():
                        fp.write(chunk)
                    fp.close()
                    messages.info(request, "Uploaded file '%s'." % filename)
                except:
                    messages.warning(request, "Failed to upload file '%s'." % filename)
            return redirect_to(request, folder.url())
    else:
        form = AddFileForm()
    return render_to_response('coconuts/add_file.html', FolderContext(request, folder, {
        'form': form,
        'title': _('Add a file')}))

def add_folder(request, path):
    try:
        folder = Folder(path)
    except Folder.DoesNotExist:
        raise Http404

    # check permissions
    if not folder.has_perm('can_write', request.user):
        return forbidden(request)

    if request.method == 'POST':
        form = AddFolderForm(request.POST)
        if form.is_valid():
            foldername = form.cleaned_data['name']
            try:
                subfolder = Folder.create(os.path.join(folder.path, foldername))
                notifications.create_folder(request.user, subfolder)
                messages.info(request, "Created folder '%s'." % foldername)
            except:
                messages.warning(request, "Failed to create folder '%s'." % foldername)
            return redirect_to(request, folder.url())
    else:
        form = AddFolderForm()
    return render_to_response('coconuts/add_folder.html', FolderContext(request, folder, {
        'form': form,
        'title': _('Create a folder')}))

def browse(request, path):
    if not path or path.endswith('/'):
        return photo_list(request, path)
    else:
        return photo_detail(request, path)

@login_required
def photo_list(request, path):
    """Show the list of photos for the given folder."""
    try:
        folder = Folder(os.path.dirname(path))
    except Folder.DoesNotExist:
        raise Http404

    # check permissions
    if not folder.has_perm('can_read', request.user):
        return forbidden(request)

    # list of sub-folders and photos
    children, files, photos, mode = folder.list()
    # keep only the children the user is allowed to read. This is only useful in '/'
    allowed_children = [x for x in children if x.has_perm('can_read', request.user)]
    return render_to_response('coconuts/photo_list.html', FolderContext(request, folder, {
        'children': allowed_children,
        'files': files,
        'mode': mode,
        'photos': photos,
        }))

@login_required
def rss(request,path):
    """Generate a Media RSS feed for the given folder."""
    folder = Folder(path)

    # check permissions
    if not folder.has_perm('can_read', request.user):
        return forbidden(request)

    # list of sub-folders and photos
    children, files, photos, mode = folder.list()
    return render_to_response('coconuts/photos.rss',
        FolderContext(request, folder, {'photos': photos}),
        mimetype="application/rss+xml")

@login_required
def photo_detail(request,path):
    """Show the detailed view for the given photo."""
    try:
        folder = Folder(os.path.dirname(path))
        photo = Photo(path)
    except (File.DoesNotExist, Folder.DoesNotExist):
        raise Http404

    # check permissions
    if not folder.has_perm('can_read', request.user):
        return forbidden(request)

    # navigation
    children, files, photos, mode = folder.list()
    return render_to_response('coconuts/photo_detail.html', FolderContext(request, folder, {
        'photo': photo,
        'size': PHOTO_SIZE,
        'nav': make_nav(photo, photos),
        }))
 
@login_required
def delete(request, path):
    """Delete the given file."""
    if not path:
        return forbidden(request)

    # navigation links
    back_url = "/"
    next_url = "/"
    if request.REQUEST.has_key('next'):
        next_url = request.REQUEST['next']
    if request.REQUEST.has_key('back'):
        back_url = request.REQUEST['back']

    # find target
    if File.isdir(path):
        target = Folder(path)
        is_folder = True
    else:
        target = File(path)
        is_folder = False

    # check permissions
    folder = Folder(os.path.dirname(target.path))
    if not folder.has_perm('can_write', request.user):
        return forbidden(request)

    # delete file then redirect user
    if request.method == 'POST':
        target.delete()
        return redirect_to(request, next_url)

    return render_to_response('coconuts/delete.html', FolderContext(request, folder, {
        'is_folder': is_folder,
        'target': target,
        'back': back_url,
        'next': next_url,
    }))

@login_required
def download(request, path):
    """Return the raw file for the given photo."""
    folder = Folder(os.path.dirname(path))

    # check permissions
    if not folder.has_perm('can_read', request.user):
        return forbidden(request)

    resp = django.views.static.serve(request,
        path,
        document_root=settings.COCONUTS_DATA_ROOT)
    resp['Content-Disposition'] = 'attachment; filename="%s"' % urlquote(os.path.basename(path))
    return resp

@login_required
def manage(request, path):
    """Manage the properties for the given folder."""

    # Check this is a folder, not a file
    if path and not (path.endswith('/') and path.count("/") == 1):
        return forbidden(request)

    # Check permissions
    folder = Folder(os.path.dirname(path))
    share = folder.share
    if not share.has_perm('can_manage', request.user):
        return forbidden(request)

    # Process submission
    if request.method == 'POST':
        # properties
        shareform = ShareForm(request.POST, instance=share)
        if shareform.is_valid():
            shareform.save()

        # access
        formset = ShareAccessFormSet(request.POST)
        if formset.is_valid():
            # access
            acls = []
            for data in formset.clean():
                acl = NamedAcl("%s:" % data['owner'])
                for perm in PERMISSIONS:
                    if data[perm]: acl.add_perm(perm)
                if acl.permissions: acls.append(acl)
            share.set_acls(acls)

            # Check we are not locking ourselves out before saving
            if not share.has_perm('can_manage', request.user):
                return forbidden(request)
            share.save()
            return redirect_to(request, folder.url())

    # fill form from database
    data = []
    for acl in share.acls():
        entry = {'owner': "%s:%s" % (acl.type, acl.name)}
        for perm in PERMISSIONS:
            entry[perm] = acl.has_perm(perm)
        data.append(entry)
    shareform = ShareForm(instance=share)
    formset = ShareAccessFormSet(initial=data)

    return render_to_response('coconuts/manage.html', FolderContext(request, folder, {
        'formset': formset,
        'share': share,
        'shareform': shareform,
        }))

@login_required
def render(request, path):
    """Return a resized version of the given photo."""
    folder = Folder(os.path.dirname(path))

    # check permissions
    if not folder.has_perm('can_read', request.user):
        return forbidden(request)

    # check the size is legitimate
    form = PhotoForm(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest()
    size = form.cleaned_data['size']

    # serve the photo
    photo = Photo(path)
    response = django.views.static.serve(request,
        photo.cache(size),
        document_root=settings.COCONUTS_CACHE_ROOT)
    response["Expires"] = http_date(time.time() + 3600 * 24 * 365)
    return response
