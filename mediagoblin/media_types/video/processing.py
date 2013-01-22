# GNU MediaGoblin -- federated, autonomous media hosting
# Copyright (C) 2011, 2012 MediaGoblin contributors.  See AUTHORS.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from tempfile import NamedTemporaryFile
import logging

from mediagoblin import mg_globals as mgg
from mediagoblin.decorators import get_workbench
from mediagoblin.processing import \
    create_pub_filepath, FilenameBuilder, BaseProcessingFail, ProgressCallback
from mediagoblin.tools.translate import lazy_pass_to_ugettext as _

from . import transcoders
from .util import skip_transcode


_log = logging.getLogger(__name__)
_log.setLevel(logging.DEBUG)


class VideoTranscodingFail(BaseProcessingFail):
    '''
    Error raised if video transcoding fails
    '''
    general_message = _(u'Video transcoding failed')


def sniff_handler(media_file, **kw):
    transcoder = transcoders.VideoTranscoder()
    data = transcoder.discover(media_file.name)

    _log.debug('Discovered: {0}'.format(data))

    if not data:
        _log.error('Could not discover {0}'.format(
                kw.get('media')))
        return False

    if data['is_video'] == True:
        return True

    return False

@get_workbench
def process_video(entry, workbench=None):
    """
    Process a video entry, transcode the queued media files (originals) and
    create a thumbnail for the entry.

    A Workbench() represents a local tempory dir. It is automatically
    cleaned up when this function exits.
    """
    video_config = mgg.global_config['media_type:mediagoblin.media_types.video']

    queued_filepath = entry.queued_media_file
    queued_filename = workbench.localized_file(
        mgg.queue_store, queued_filepath,
        'source')
    name_builder = FilenameBuilder(queued_filename)

    medium_filepath = create_pub_filepath(
        entry, name_builder.fill('{basename}-640p.webm'))

    thumbnail_filepath = create_pub_filepath(
        entry, name_builder.fill('{basename}.thumbnail.jpg'))

    # Create a temporary file for the video destination (cleaned up with workbench)
    tmp_dst = NamedTemporaryFile(dir=workbench.dir, delete=False)
    with tmp_dst:
        # Transcode queued file to a VP8/vorbis file that fits in a 640x640 square
        progress_callback = ProgressCallback(entry)

        dimensions = (
            mgg.global_config['media:medium']['max_width'],
            mgg.global_config['media:medium']['max_height'])

        metadata = transcoders.VideoTranscoder().discover(queued_filename)

        if skip_transcode(metadata):
            _log.debug('Skipping transcoding')
            # Just push the submitted file to the tmp_dst
            open(tmp_dst.name, 'wb').write(open(queued_filename, 'rb').read())

            dst_dimensions = metadata['videowidth'], metadata['videoheight']
        else:
            transcoder = transcoders.VideoTranscoder()

            transcoder.transcode(queued_filename, tmp_dst.name,
                    vp8_quality=video_config['vp8_quality'],
                    vp8_threads=video_config['vp8_threads'],
                    vorbis_quality=video_config['vorbis_quality'],
                    progress_callback=progress_callback,
                    dimensions=dimensions)

            dst_dimensions = transcoder.dst_data.videowidth,\
                    transcoder.dst_data.videoheight

        # Push transcoded video to public storage
        _log.debug('Saving medium...')
        mgg.public_store.copy_local_to_storage(tmp_dst.name, medium_filepath)
        _log.debug('Saved medium')

        entry.media_files['webm_640'] = medium_filepath

        # Save the width and height of the transcoded video
        entry.media_data_init(
            width=dst_dimensions[0],
            height=dst_dimensions[1])

    # Temporary file for the video thumbnail (cleaned up with workbench)
    tmp_thumb = NamedTemporaryFile(dir=workbench.dir, suffix='.jpg', delete=False)

    with tmp_thumb:
        # Create a thumbnail.jpg that fits in a 180x180 square
        transcoders.VideoThumbnailerMarkII(
                queued_filename,
                tmp_thumb.name,
                180)

        # Push the thumbnail to public storage
        _log.debug('Saving thumbnail...')
        mgg.public_store.copy_local_to_storage(tmp_thumb.name, thumbnail_filepath)
        entry.media_files['thumb'] = thumbnail_filepath

    if video_config['keep_original']:
        # Push original file to public storage
        _log.debug('Saving original...')
        original_filepath = create_pub_filepath(entry, queued_filepath[-1])
        mgg.public_store.copy_local_to_storage(queued_filename, original_filepath)
        entry.media_files['original'] = original_filepath

    mgg.queue_store.delete_file(queued_filepath)
